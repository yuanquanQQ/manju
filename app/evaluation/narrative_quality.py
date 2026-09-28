"""Deterministic narrative quality report for one generated episode."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.novel import ChapterAnalysis
from app.domain.storyboard import Episode


class NarrativeMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    detail: str


class NarrativeQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    episode_number: int = Field(ge=1)
    passed: bool
    overall_score: float = Field(ge=0.0, le=1.0)
    important_event_coverage: NarrativeMetric
    scene_contract_completeness: NarrativeMetric
    emotional_variation: NarrativeMetric
    shot_binding_coverage: NarrativeMetric
    camera_diversity: NarrativeMetric
    dialogue_traceability: NarrativeMetric
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _ratio(found: int, total: int) -> float:
    return 1.0 if total == 0 else round(found / total, 4)


def evaluate_narrative_quality(
    episode: Episode,
    analysis: ChapterAnalysis,
) -> NarrativeQualityReport:
    plan = episode.narrative_plan
    important_ids = {event.event_id for event in analysis.events if event.importance >= 4}
    covered_ids = {
        event_id
        for shot in episode.shots
        for event_id in shot.narrative_binding.source_event_ids
    }
    event_score = _ratio(len(important_ids & covered_ids), len(important_ids))

    complete_scenes = 0
    if plan:
        for scene in plan.scenes:
            required = (
                scene.purpose,
                scene.goal,
                scene.obstacle,
                scene.stakes,
                scene.turn,
                scene.outcome,
                scene.value_before,
                scene.value_after,
            )
            complete_scenes += int(all(value.strip() for value in required))
    scene_score = _ratio(complete_scenes, len(plan.scenes) if plan else 1)

    emotions = {shot.emotion.strip().casefold() for shot in episode.shots if shot.emotion.strip()}
    emotion_target = min(3, len(episode.shots))
    emotion_score = _ratio(min(len(emotions), emotion_target), emotion_target)

    bound_shots = sum(
        shot.narrative_binding.scene_number > 0
        and bool(shot.narrative_binding.dramatic_purpose.strip())
        and shot.narrative_binding.value_before.strip()
        != shot.narrative_binding.value_after.strip()
        for shot in episode.shots
    )
    binding_score = _ratio(bound_shots, len(episode.shots))

    camera_patterns = {
        (shot.camera_angle.strip().casefold(), shot.camera_movement.strip().casefold())
        for shot in episode.shots
    }
    camera_target = min(4, len(episode.shots))
    camera_score = _ratio(min(len(camera_patterns), camera_target), camera_target)

    planned_dialogues = [
        dialogue
        for scene in (plan.scenes if plan else [])
        for dialogue in scene.dialogues
    ]
    spoken_text = "\n".join(shot.dialogue for shot in episode.shots)
    traced_dialogues = sum(
        dialogue.adapted_text in spoken_text or dialogue.source_text in spoken_text
        for dialogue in planned_dialogues
    )
    dialogue_score = _ratio(traced_dialogues, len(planned_dialogues))

    failures: list[str] = []
    if event_score < 1.0:
        failures.append("重要事件没有全部进入编剧计划")
    if scene_score < 1.0:
        failures.append("场次戏剧契约字段不完整")
    if binding_score < 0.9:
        failures.append("少于 90% 的镜头绑定了有效戏剧场次")
    if dialogue_score < 0.8:
        failures.append("少于 80% 的改编对白可追溯到成片镜头")

    warnings: list[str] = []
    if emotion_score < 0.67:
        warnings.append("镜头情绪变化偏少")
    if camera_score < 0.75:
        warnings.append("景别与运镜组合偏单一")

    scores = (
        event_score,
        scene_score,
        emotion_score,
        binding_score,
        camera_score,
        dialogue_score,
    )
    return NarrativeQualityReport(
        episode_number=episode.episode_number,
        passed=not failures,
        overall_score=round(sum(scores) / len(scores), 4),
        important_event_coverage=NarrativeMetric(
            score=event_score,
            detail=f"已覆盖 {len(important_ids & covered_ids)}/{len(important_ids)} 个重要事件",
        ),
        scene_contract_completeness=NarrativeMetric(
            score=scene_score,
            detail=f"完整场次 {complete_scenes}/{len(plan.scenes) if plan else 0}",
        ),
        emotional_variation=NarrativeMetric(
            score=emotion_score,
            detail=f"共 {len(emotions)} 种镜头情绪",
        ),
        shot_binding_coverage=NarrativeMetric(
            score=binding_score,
            detail=f"有效绑定 {bound_shots}/{len(episode.shots)} 个镜头",
        ),
        camera_diversity=NarrativeMetric(
            score=camera_score,
            detail=f"共 {len(camera_patterns)} 种景别/运镜组合",
        ),
        dialogue_traceability=NarrativeMetric(
            score=dialogue_score,
            detail=f"可追溯 {traced_dialogues}/{len(planned_dialogues)} 条计划对白",
        ),
        failures=failures,
        warnings=warnings,
    )
