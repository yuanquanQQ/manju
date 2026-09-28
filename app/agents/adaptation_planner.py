"""Choose episode boundaries from dramatic progression across chapters."""

from __future__ import annotations

from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.core.prompts import load_prompt
from app.domain.narrative import (
    AdaptationOutline,
    EpisodeSourceGroup,
    NarrativePlanProvenance,
)
from app.domain.novel import ChapterAnalysis, StandardChapter

PROMPT_VERSION = "adaptation-outline-v1"

ADAPTATION_OUTLINE_PROMPT = load_prompt("adaptation_outline")


class _OutlineDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[EpisodeSourceGroup] = Field(min_length=1)


class AdaptationPlanningError(RuntimeError):
    pass


def _validate_outline(
    draft: _OutlineDraft,
    chapter_ids: list[str],
    first_episode_number: int,
) -> None:
    flattened = [chapter_id for group in draft.groups for chapter_id in group.chapter_ids]
    if flattened != chapter_ids:
        raise ValueError("组集结果必须按原顺序完整覆盖每个章节且不得重复")
    expected_numbers = list(
        range(first_episode_number, first_episode_number + len(draft.groups))
    )
    if [group.episode_number for group in draft.groups] != expected_numbers:
        raise ValueError("组集编号必须从起始集号连续递增")


def _outline_context(
    chapters: list[StandardChapter],
    analyses: list[ChapterAnalysis],
) -> str:
    blocks: list[str] = []
    for chapter, analysis in zip(chapters, analyses, strict=True):
        events = "；".join(
            f"[{event.importance}]{event.summary}" for event in analysis.events
        )
        blocks.append(
            f"{chapter.chapter_id}｜{chapter.title}｜{len(chapter.content)}字\n"
            f"摘要：{analysis.summary}\n事件：{events}"
        )
    return "\n\n".join(blocks)


def plan_adaptation_outline(
    chapters: list[StandardChapter],
    analyses: list[ChapterAnalysis],
    *,
    llm: StructuredLLM | None = None,
    first_episode_number: int | None = None,
) -> AdaptationOutline:
    if not chapters or len(chapters) != len(analyses):
        raise ValueError("组集规划需要一一对应的章节与分析")
    chapter_ids = [chapter.chapter_id for chapter in chapters]
    if [analysis.chapter_id for analysis in analyses] != chapter_ids:
        raise ValueError("组集规划的章节与分析顺序不一致")
    start_number = first_episode_number or chapters[0].order
    client = llm or OpenAICompatibleLLM()
    combined_hash = sha256(
        "|".join(analysis.provenance.input_hash for analysis in analyses).encode()
    ).hexdigest()
    if len(chapters) == 1:
        group = EpisodeSourceGroup(
            episode_number=start_number,
            episode_title=chapters[0].title,
            chapter_ids=chapter_ids,
            boundary_reason="单章已形成独立冲突与转折",
            opening_hook=analyses[0].summary or chapters[0].title,
            closing_hook=analyses[0].events[-1].summary if analyses[0].events else chapters[0].title,
        )
        return AdaptationOutline(
            groups=[group],
            provenance=NarrativePlanProvenance(
                model=client.model_name,
                prompt_version=PROMPT_VERSION,
                input_hash=combined_hash,
            ),
        )

    prompt = (
        f"起始集号：{start_number}\n"
        f"待规划章节：{','.join(chapter_ids)}\n\n"
        f"章节材料：\n{_outline_context(chapters, analyses)}"
    )
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            value = client.complete(
                system_prompt=ADAPTATION_OUTLINE_PROMPT,
                user_prompt=prompt,
                json_schema=_OutlineDraft.model_json_schema(),
            )
            draft = _OutlineDraft.model_validate(value)
            _validate_outline(draft, chapter_ids, start_number)
            return AdaptationOutline(
                groups=draft.groups,
                provenance=NarrativePlanProvenance(
                    model=client.model_name,
                    prompt_version=PROMPT_VERSION,
                    input_hash=combined_hash,
                ),
            )
        except (ValidationError, ValueError, RuntimeError) as exc:
            last_error = exc
            prompt += f"\n\n上次组集未通过校验：{exc}。请完整修正。"
    raise AdaptationPlanningError(f"跨章节组集失败：{last_error}") from last_error
