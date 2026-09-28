"""分镜生成流水线。

从已分析章节 → 导演 Agent → 分镜脚本（JSON 输出）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.agents.adaptation_planner import (
    PROMPT_VERSION as OUTLINE_PROMPT_VERSION,
)
from app.agents.adaptation_planner import plan_adaptation_outline
from app.agents.director import direct_chapter
from app.agents.story_planner import PROMPT_VERSION as STORY_PLAN_PROMPT_VERSION
from app.agents.story_planner import plan_episode
from app.compiler.adaptation_material import combine_adaptation_material
from app.compiler.repository import (
    build_analysis_from_raw_tables,
    find_reusable_analysis,
    list_compiled_chapters,
)
from app.core.files import atomic_write_json
from app.core.logger import logger
from app.domain.narrative import (
    AdaptationOutline,
    EpisodePlan,
    NarrativePlanProvenance,
    StoryBible,
)
from app.domain.novel import ChapterAnalysis, StandardChapter
from app.domain.storyboard import Episode
from app.evaluation.narrative_quality import evaluate_narrative_quality
from app.pipeline.story_bible import evolve_story_bible, rebuild_story_bible
from app.pipeline.story_plan_store import save_episode_plan

_PROFILE_PLACEHOLDERS = ("已锁定", "按设定", "待定", "未提供")


def _usable_profile(value: object) -> bool:
    profile = str(value or "").strip()
    return len(profile) >= 80 and not any(
        term in profile for term in _PROFILE_PLACEHOLDERS
    )


def _collect_fingerprints_from_episodes(
    output_dir: Path,
) -> dict[str, str]:
    """Scan all episode files for the most recent character fingerprints.

    Fingerprints are passed from episode to episode so the same character keeps
    the same identity lock. When episodes are generated out of order the chain
    breaks; this fallback reads fingerprints from every existing episode file
    so the identity is still inherited.
    """
    collected: dict[str, str] = {}
    for episode_file in sorted(output_dir.glob("episode_*.json")):
        try:
            data = json.loads(episode_file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        fingerprints = data.get("character_visual_fingerprints")
        if isinstance(fingerprints, dict):
            for name, value in fingerprints.items():
                if isinstance(name, str) and isinstance(value, str) and value:
                    collected[name] = value
    return collected


def _collect_profiles_from_episodes(output_dir: Path) -> dict[str, str]:
    """Collect approved cast briefs so recurring characters are not redesigned."""

    collected: dict[str, str] = {}
    for episode_file in sorted(output_dir.glob("episode_*.json")):
        try:
            data = json.loads(episode_file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        profiles = data.get("character_profiles") if isinstance(data, dict) else None
        if not isinstance(profiles, dict):
            continue
        for name, value in profiles.items():
            if isinstance(name, str) and isinstance(value, str) and _usable_profile(value):
                collected[name] = value
    return collected


def _load_reusable_story_plan(
    plan_path: Path,
    *,
    model: str,
    input_hash: str,
) -> EpisodePlan | None:
    if not plan_path.is_file():
        return None
    try:
        plan = EpisodePlan.model_validate_json(
            plan_path.read_text(encoding="utf-8-sig")
        )
    except Exception as exc:
        logger.warning(f"编剧计划无法读取，将重新生成: {plan_path} ({exc})")
        return None
    provenance = plan.provenance
    if (
        provenance.model != model
        or provenance.prompt_version != STORY_PLAN_PROMPT_VERSION
        or provenance.input_hash != input_hash
    ):
        return None
    return plan


def _load_previous_story_plan(
    plan_dir: Path,
    episode_number: int,
) -> EpisodePlan | None:
    for path in sorted(plan_dir.glob("episode_*.json"), reverse=True):
        try:
            plan = EpisodePlan.model_validate_json(
                path.read_text(encoding="utf-8-sig")
            )
        except Exception:
            continue
        if plan.episode_number < episode_number:
            return plan
    return None


def _load_story_bible(path: Path) -> StoryBible:
    if not path.is_file():
        return StoryBible()
    try:
        return StoryBible.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"故事圣经无法读取: {path} ({exc})") from exc


def _story_bible_before_episode(
    plan_dir: Path,
    existing_bible: StoryBible,
    episode_number: int,
) -> StoryBible:
    plans: list[EpisodePlan] = []
    for path in sorted(plan_dir.glob("episode_*.json")):
        try:
            plan = EpisodePlan.model_validate_json(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise ValueError(f"编剧计划无法用于重建故事圣经: {path} ({exc})") from exc
        if plan.episode_number < episode_number:
            plans.append(plan)
    return rebuild_story_bible(plans, world_rules=existing_bible.world_rules)


def _load_analyses(
    chapters: list[StandardChapter],
    client: StructuredLLM,
) -> list[ChapterAnalysis]:
    analyses: list[ChapterAnalysis] = []
    for chapter in chapters:
        analysis = find_reusable_analysis(
            chapter,
            model=client.model_name,
            prompt_version="chapter-analysis-v1",
        )
        if analysis is None:
            analysis = build_analysis_from_raw_tables(
                chapter,
                model=client.model_name,
                prompt_version="chapter-analysis-v1",
            )
        if analysis is None:
            raise ValueError(f"章节 {chapter.chapter_id} 无可用分析数据")
        analyses.append(analysis)
    return analyses


def _plan_outline_incrementally(
    outline_path: Path,
    chapters: list[StandardChapter],
    analyses: list[ChapterAnalysis],
    client: StructuredLLM,
) -> AdaptationOutline:
    requested_ids = [chapter.chapter_id for chapter in chapters]
    existing_groups = []
    if outline_path.is_file():
        try:
            existing = AdaptationOutline.model_validate_json(
                outline_path.read_text(encoding="utf-8-sig")
            )
        except Exception:
            existing = None
        if (
            existing
            and existing.provenance.model == client.model_name
            and existing.provenance.prompt_version == OUTLINE_PROMPT_VERSION
        ):
            flattened: list[str] = []
            for group in existing.groups:
                candidate = [*flattened, *group.chapter_ids]
                if requested_ids[: len(candidate)] != candidate:
                    break
                flattened = candidate
                existing_groups.append(group)
                if len(flattened) == len(requested_ids):
                    return existing.model_copy(
                        update={"groups": list(existing_groups)}
                    )

    covered = sum(len(group.chapter_ids) for group in existing_groups)
    next_episode = (
        existing_groups[-1].episode_number + 1
        if existing_groups
        else chapters[0].order
    )
    new_groups = []
    for offset in range(covered, len(chapters), 12):
        chapter_batch = chapters[offset : offset + 12]
        analysis_batch = analyses[offset : offset + 12]
        batch = plan_adaptation_outline(
            chapter_batch,
            analysis_batch,
            llm=client,
            first_episode_number=next_episode,
        )
        new_groups.extend(batch.groups)
        next_episode = new_groups[-1].episode_number + 1
    combined_hash = sha256(
        "|".join(analysis.provenance.input_hash for analysis in analyses).encode()
    ).hexdigest()
    outline = AdaptationOutline(
        groups=[*existing_groups, *new_groups],
        provenance=NarrativePlanProvenance(
            model=client.model_name,
            prompt_version=OUTLINE_PROMPT_VERSION,
            input_hash=combined_hash,
        ),
    )
    atomic_write_json(outline_path, outline.model_dump(mode="json"))
    return outline


def generate_storyboard(
    project_root: str | Path,
    *,
    limit: int = 0,
    start: int = 0,
    end: int = 0,
    llm: StructuredLLM | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[Episode]:
    """从已分析章节生成分镜。"""
    root = Path(project_root)
    client = llm or OpenAICompatibleLLM()
    chapters = list_compiled_chapters(limit=limit, start=start, end=end)

    if not chapters:
        raise ValueError("没有可编译的标准章节，请先执行 import-novel + compile")

    output_dir = root / "production" / "episodes"
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_dir = root / "production" / "story_plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    quality_dir = root / "production" / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    analyses = _load_analyses(chapters, client)
    outline = _plan_outline_incrementally(
        root / "production" / "adaptation_outline.json",
        chapters,
        analyses,
        client,
    )
    chapter_by_id = {chapter.chapter_id: chapter for chapter in chapters}
    analysis_by_id = {analysis.chapter_id: analysis for analysis in analyses}
    story_bible_path = root / "production" / "story_bible.json"
    stored_bible = _load_story_bible(story_bible_path)
    story_bible = _story_bible_before_episode(
        plan_dir,
        stored_bible,
        outline.groups[0].episode_number,
    )
    episodes: list[Episode] = []

    for index, group in enumerate(outline.groups, start=1):
        grouped_chapters = [chapter_by_id[chapter_id] for chapter_id in group.chapter_ids]
        grouped_analyses = [analysis_by_id[chapter_id] for chapter_id in group.chapter_ids]
        material = combine_adaptation_material(grouped_chapters, grouped_analyses)
        if progress_callback:
            progress_callback(
                index - 1,
                len(outline.groups),
                f"正在编剧第 {group.episode_number} 集：{group.episode_title}",
            )
        logger.info(
            f"生成分镜: episode_{group.episode_number:03d} "
            f"({','.join(group.chapter_ids)})"
        )

        try:
            episode_path = output_dir / f"episode_{group.episode_number:03d}.json"
            plan_path = plan_dir / f"episode_{group.episode_number:03d}.json"
            existing: dict[str, object] = {}
            if episode_path.is_file():
                loaded = json.loads(
                    episode_path.read_text(encoding="utf-8-sig")
                )
                if isinstance(loaded, dict):
                    existing = loaded
            # When episodes are generated out of order (e.g. episode 10 before
            # 5), the fingerprint chain breaks because fingerprints are only
            # read from the same episode file. Fall back to scanning all
            # existing episode files for the most recent fingerprints.
            existing_fingerprints = (
                dict(existing["character_visual_fingerprints"])
                if isinstance(
                    existing.get("character_visual_fingerprints"), dict
                )
                else {}
            )
            if not existing_fingerprints:
                existing_fingerprints = _collect_fingerprints_from_episodes(
                    output_dir
                )
            existing_profiles = (
                {
                    str(name): str(value)
                    for name, value in existing["character_profiles"].items()
                    if _usable_profile(value)
                }
                if isinstance(existing.get("character_profiles"), dict)
                else {}
            )
            if not existing_profiles:
                existing_profiles = _collect_profiles_from_episodes(output_dir)
            episode_plan = _load_reusable_story_plan(
                plan_path,
                model=client.model_name,
                input_hash=material.analysis.provenance.input_hash,
            )
            if episode_plan is None:
                episode_plan = plan_episode(
                    material.analysis,
                    material.source_text,
                    llm=client,
                    episode_number=group.episode_number,
                    episode_title=group.episode_title,
                    previous_plan=_load_previous_story_plan(
                        plan_dir,
                        group.episode_number,
                    ),
                    story_bible=story_bible,
                    source_chapter_ids=material.chapter_ids,
                )
                save_episode_plan(root, group.episode_number, episode_plan)
            episode = direct_chapter(
                material.analysis,
                llm=client,
                episode_number=group.episode_number,
                episode_title=episode_plan.episode_title,
                source_text=material.source_text,
                character_profiles=existing_profiles,
                character_visual_fingerprints=existing_fingerprints,
                character_styles=(
                    dict(existing["character_styles"])
                    if isinstance(existing.get("character_styles"), dict)
                    else {}
                ),
                character_generation_presets=(
                    dict(existing["character_generation_presets"])
                    if isinstance(
                        existing.get("character_generation_presets"),
                        dict,
                    )
                    else {}
                ),
                episode_plan=episode_plan,
            )
            episodes.append(episode)
            atomic_write_json(episode_path, episode.model_dump(mode="json"))
            quality_report = evaluate_narrative_quality(episode, material.analysis)
            atomic_write_json(
                quality_dir / f"episode_{group.episode_number:03d}.json",
                quality_report.model_dump(mode="json"),
            )
            if not quality_report.passed:
                raise ValueError(
                    "叙事质量门禁未通过：" + "；".join(quality_report.failures)
                )
            story_bible = evolve_story_bible(story_bible, episode_plan)
            atomic_write_json(
                story_bible_path,
                story_bible.model_dump(mode="json"),
            )
            logger.info(
                f"已保存: {episode_path} "
                f"({len(episode.shots)} 个镜头, "
                f"总时长 {sum(s.duration_seconds for s in episode.shots):.0f}s)"
            )

        except Exception as exc:
            logger.error(f"第 {group.episode_number} 集分镜生成失败: {exc}")
            raise RuntimeError(
                f"第 {group.episode_number} 集分镜生成失败，已停止批次：{exc}"
            ) from exc
        if progress_callback:
            progress_callback(
                index,
                len(outline.groups),
                f"已处理 {index}/{len(outline.groups)} 集分镜",
            )

    return episodes
