"""Professional dramatic planning before visual shot generation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.core.prompts import load_prompt
from app.domain.narrative import (
    CharacterArcBeat,
    EpisodePlan,
    NarrativePlanProvenance,
    ScenePlan,
    StoryBible,
    StoryThreadUpdate,
)
from app.domain.novel import ChapterAnalysis

PROMPT_VERSION = "episode-story-plan-v1"

STORY_PLANNER_SYSTEM_PROMPT = load_prompt("story_planner")


class _EpisodePlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_title: str = Field(min_length=1, max_length=200)
    logline: str = Field(min_length=8, max_length=500)
    protagonist: str = Field(min_length=1, max_length=80)
    episode_goal: str = Field(min_length=2, max_length=300)
    central_conflict: str = Field(min_length=2, max_length=300)
    stakes: str = Field(min_length=2, max_length=300)
    opening_hook: str = Field(min_length=2, max_length=300)
    scenes: list[ScenePlan] = Field(min_length=2, max_length=8)
    climax: str = Field(min_length=2, max_length=300)
    cliffhanger: str = Field(min_length=2, max_length=300)
    emotional_arc: list[str] = Field(min_length=2, max_length=8)
    character_arcs: list[CharacterArcBeat] = Field(default_factory=list)
    thread_updates: list[StoryThreadUpdate] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)


class StoryPlanningError(RuntimeError):
    pass


def _analysis_context(analysis: ChapterAnalysis) -> str:
    lines = [f"章节摘要：{analysis.summary}", "事件："]
    lines.extend(
        f"- {event.event_id}｜重要度{event.importance}｜{event.summary}｜结果：{event.result}"
        for event in analysis.events
    )
    lines.append("对白：")
    lines.extend(
        f"- {dialogue.dialogue_id}｜{dialogue.speaker}：{dialogue.text}｜情绪：{dialogue.emotion}"
        for dialogue in analysis.dialogues
    )
    lines.append("状态变化：")
    lines.extend(
        f"- {change.entity}.{change.attribute}：{change.before} → {change.after}"
        for change in analysis.state_changes
    )
    if analysis.adaptation_notes:
        lines.append("已有改编提示：" + "；".join(analysis.adaptation_notes))
    return "\n".join(lines)


def _previous_context(
    previous_plan: EpisodePlan | None,
    story_bible: StoryBible | None,
) -> str:
    if previous_plan is None and story_bible is None:
        return "无上一集计划或故事圣经。"
    bible_arcs = "；".join(
        f"{name}:{state.arc_state}"
        for name, state in (story_bible.characters.items() if story_bible else [])
    )
    bible_threads = "；".join(
        f"{name}:{state.state}({state.status})"
        for name, state in (story_bible.threads.items() if story_bible else [])
        if state.status != "paid_off"
    )
    if previous_plan is None:
        return (
            f"长期人物状态：{bible_arcs or '无记录'}\n"
            f"长期未解决线索：{bible_threads or '无记录'}"
        )
    arcs = "；".join(
        f"{arc.character}:{arc.change}" for arc in previous_plan.character_arcs
    )
    threads = "；".join(previous_plan.unresolved_threads)
    return (
        f"上一集结尾：{previous_plan.cliffhanger}\n"
        f"人物变化：{arcs or '无记录'}\n"
        f"未解决线索：{threads or '无记录'}\n"
        f"长期人物状态：{bible_arcs or '无记录'}\n"
        f"长期未解决线索：{bible_threads or '无记录'}"
    )


def _validate_source_anchors(
    draft: _EpisodePlanDraft,
    analysis: ChapterAnalysis,
    source_text: str,
) -> None:
    event_ids = {event.event_id for event in analysis.events}
    required_event_ids = {
        event.event_id for event in analysis.events if event.importance >= 4
    }
    used_event_ids = {
        event_id for scene in draft.scenes for event_id in scene.source_event_ids
    }
    unknown_event_ids = used_event_ids - event_ids
    if unknown_event_ids:
        raise ValueError(f"计划引用了不存在的事件：{'、'.join(sorted(unknown_event_ids))}")
    missing_event_ids = required_event_ids - used_event_ids
    if missing_event_ids:
        raise ValueError(f"重要事件未进入场次：{'、'.join(sorted(missing_event_ids))}")

    dialogue_by_id = {
        dialogue.dialogue_id: dialogue for dialogue in analysis.dialogues
    }
    for scene in draft.scenes:
        for planned in scene.dialogues:
            source = dialogue_by_id.get(planned.source_dialogue_id)
            if source is None:
                raise ValueError(
                    f"改编对白引用不存在的记录：{planned.source_dialogue_id}"
                )
            if planned.speaker != source.speaker or planned.source_text != source.text:
                raise ValueError(
                    f"改编对白与原始说话人或原文不一致：{planned.source_dialogue_id}"
                )
            if planned.source_text not in source_text:
                raise ValueError(
                    f"改编对白无法在章节原文定位：{planned.source_dialogue_id}"
                )


def format_episode_plan(plan: EpisodePlan) -> str:
    lines = [
        f"集标题：{plan.episode_title}",
        f"一句话故事：{plan.logline}",
        f"主角目标：{plan.protagonist}｜{plan.episode_goal}",
        f"核心冲突：{plan.central_conflict}",
        f"失败代价：{plan.stakes}",
        f"开场钩子：{plan.opening_hook}",
        f"高潮：{plan.climax}",
        f"集尾悬念：{plan.cliffhanger}",
        "情绪轨迹：" + " → ".join(plan.emotional_arc),
        "场次计划：",
    ]
    for scene in plan.scenes:
        lines.append(
            f"场{scene.scene_number}《{scene.title}》｜功能：{scene.purpose}｜"
            f"目标：{scene.goal}｜阻力：{scene.obstacle}｜策略：{scene.strategy}｜"
            f"转折：{scene.turn}｜结果：{scene.outcome}｜"
            f"价值：{scene.value_before}→{scene.value_after}｜"
            f"情绪：{scene.emotion_start}→{scene.emotion_end}｜"
            f"来源事件：{','.join(scene.source_event_ids) or '无'}｜"
            f"镜头预算：{scene.estimated_shots}"
        )
        for dialogue in scene.dialogues:
            lines.append(
                f"  改编对白｜{dialogue.speaker}：{dialogue.adapted_text}｜"
                f"意图：{dialogue.intent}｜潜台词：{dialogue.subtext}"
            )
    return "\n".join(lines)


def planned_dialogue_pairs(plan: EpisodePlan) -> set[tuple[str, str]]:
    return {
        (dialogue.speaker, dialogue.adapted_text)
        for scene in plan.scenes
        for dialogue in scene.dialogues
    }


def plan_episode(
    analysis: ChapterAnalysis,
    source_text: str,
    *,
    llm: StructuredLLM | None = None,
    episode_number: int = 1,
    episode_title: str = "",
    previous_plan: EpisodePlan | None = None,
    story_bible: StoryBible | None = None,
    source_chapter_ids: list[str] | None = None,
) -> EpisodePlan:
    client = llm or OpenAICompatibleLLM()
    prompt = (
        f"请为第 {episode_number} 集制定可执行的职业编剧计划。\n"
        f"暂定标题：{episode_title or '未定'}\n\n"
        f"上一集连续性：\n{_previous_context(previous_plan, story_bible)}\n\n"
        f"结构化材料：\n{_analysis_context(analysis)}\n\n"
        f"章节原文：\n{source_text[:16000]}"
    )
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            value = client.complete(
                system_prompt=STORY_PLANNER_SYSTEM_PROMPT,
                user_prompt=prompt,
                json_schema=_EpisodePlanDraft.model_json_schema(),
            )
            draft = _EpisodePlanDraft.model_validate(value)
            _validate_source_anchors(draft, analysis, source_text)
            return EpisodePlan(
                episode_number=episode_number,
                episode_title=draft.episode_title or episode_title or f"第 {episode_number} 集",
                source_chapter_ids=source_chapter_ids or [analysis.chapter_id],
                provenance=NarrativePlanProvenance(
                    model=client.model_name,
                    prompt_version=PROMPT_VERSION,
                    input_hash=analysis.provenance.input_hash,
                ),
                **draft.model_dump(exclude={"episode_title"}),
            )
        except (ValidationError, ValueError, RuntimeError) as exc:
            last_error = exc
            prompt += f"\n\n上次计划未通过校验：{exc}。请完整修正后重新输出。"
    raise StoryPlanningError(
        f"章节 {analysis.chapter_id} 的职业编剧计划生成失败：{last_error}"
    ) from last_error
