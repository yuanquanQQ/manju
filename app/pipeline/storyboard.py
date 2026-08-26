"""分镜生成流水线。

从已分析章节 → 导演 Agent → 分镜脚本（JSON 输出）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.agents.director import direct_chapter
from app.compiler.repository import (
    build_analysis_from_raw_tables,
    find_reusable_analysis,
    list_compiled_chapters,
)
from app.core.files import atomic_write_json
from app.core.logger import logger
from app.domain.storyboard import Episode


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

    episodes: list[Episode] = []

    for index, chapter in enumerate(chapters, start=1):
        if progress_callback:
            progress_callback(
                index - 1,
                len(chapters),
                f"正在生成 {chapter.title} 的分镜",
            )
        logger.info(f"生成分镜: {chapter.chapter_id} ({chapter.title})")

        # 复用已有分析（优先 ChapterAnalysisRun，回退原始表）
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
            logger.warning(f"章节 {chapter.chapter_id} 无可用分析数据，跳过")
            continue

        try:
            episode_path = output_dir / f"episode_{chapter.order:03d}.json"
            existing: dict[str, object] = {}
            if episode_path.is_file():
                loaded = json.loads(
                    episode_path.read_text(encoding="utf-8-sig")
                )
                if isinstance(loaded, dict):
                    existing = loaded
            episode = direct_chapter(
                analysis,
                llm=client,
                episode_number=chapter.order,
                episode_title=chapter.title,
                source_text=chapter.content,
                character_profiles=(
                    dict(existing["character_profiles"])
                    if isinstance(existing.get("character_profiles"), dict)
                    else {}
                ),
                character_visual_fingerprints=(
                    dict(existing["character_visual_fingerprints"])
                    if isinstance(
                        existing.get("character_visual_fingerprints"),
                        dict,
                    )
                    else {}
                ),
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
            )
            episodes.append(episode)

            # 保存到文件
            atomic_write_json(episode_path, episode.model_dump(mode="json"))
            logger.info(
                f"已保存: {episode_path} "
                f"({len(episode.shots)} 个镜头, "
                f"总时长 {sum(s.duration_seconds for s in episode.shots):.0f}s)"
            )

        except Exception as exc:
            logger.error(f"章节 {chapter.chapter_id} 分镜生成失败: {exc}")
        if progress_callback:
            progress_callback(
                index,
                len(chapters),
                f"已处理 {index}/{len(chapters)} 集分镜",
            )

    return episodes
