from __future__ import annotations

from app.domain.narrative import EpisodePlan, NarrativePlanProvenance, ScenePlan
from app.domain.novel import (
    AnalysisProvenance,
    ChapterAnalysis,
    EvidenceSpan,
    NarrativeEvent,
)
from app.domain.storyboard import Episode, Shot, ShotNarrativeBinding
from app.evaluation.narrative_quality import evaluate_narrative_quality


def _scene(number: int, event_id: str) -> ScenePlan:
    return ScenePlan(
        scene_number=number,
        title=f"场次{number}",
        source_event_ids=[event_id],
        purpose="迫使主角做出不可撤回的选择",
        location="山门",
        goal="取得通行资格",
        obstacle="守门人拒绝放行",
        stakes="错过救人时机",
        strategy="以证据迫使对方让步",
        turn="证据暴露更大的阴谋",
        outcome="主角进入山门",
        value_before="受阻",
        value_after="通行",
        emotion_start="焦灼",
        emotion_end="警觉",
        estimated_shots=2,
        estimated_duration_seconds=12,
    )


def _plan() -> EpisodePlan:
    return EpisodePlan(
        episode_number=1,
        episode_title="山门",
        source_chapter_ids=["ch_000001"],
        logline="秦风必须突破山门阻拦，及时赶去救人。",
        protagonist="秦风",
        episode_goal="进入山门",
        central_conflict="守门人奉命拖延秦风",
        stakes="同伴可能因此丧命",
        opening_hook="求救信突然染血",
        scenes=[_scene(1, "event-1"), _scene(2, "event-2")],
        climax="秦风公开关键证据",
        cliffhanger="证据指向宗门长老",
        emotional_arc=["焦灼", "短暂胜利", "警觉"],
        provenance=NarrativePlanProvenance(
            model="test",
            prompt_version="episode-story-plan-v1",
            input_hash="hash",
        ),
    )


def _analysis() -> ChapterAnalysis:
    events = []
    for index in (1, 2):
        events.append(
            NarrativeEvent(
                event_id=f"event-{index}",
                sequence_index=index - 1,
                summary=f"关键事件{index}",
                importance=5,
                evidence=EvidenceSpan(
                    chapter_id="ch_000001",
                    start=index - 1,
                    end=index,
                    quote="证",
                ),
                confidence=1,
            )
        )
    return ChapterAnalysis(
        chapter_id="ch_000001",
        events=events,
        provenance=AnalysisProvenance(
            model="test",
            prompt_version="chapter-analysis-v1",
            input_hash="hash",
            chunk_count=1,
        ),
    )


def _shot(number: int, scene_number: int, emotion: str, camera: str) -> Shot:
    scene = _plan().scenes[scene_number - 1]
    return Shot(
        shot_number=number,
        scene_description=f"镜头{number}",
        emotion=emotion,
        camera_angle=camera,
        camera_movement="static" if number % 2 else "tracking",
        narrative_binding=ShotNarrativeBinding(
            scene_number=scene_number,
            dramatic_purpose=scene.purpose,
            source_event_ids=scene.source_event_ids,
            value_before=scene.value_before,
            value_after=scene.value_after,
        ),
    )


def test_professional_episode_passes_narrative_quality_gate() -> None:
    episode = Episode(
        episode_number=1,
        narrative_plan=_plan(),
        shots=[
            _shot(1, 1, "焦灼", "close-up"),
            _shot(2, 1, "压迫", "medium shot"),
            _shot(3, 2, "释然", "wide shot"),
            _shot(4, 2, "警觉", "over-shoulder"),
        ],
    )

    report = evaluate_narrative_quality(episode, _analysis())

    assert report.passed is True
    assert report.overall_score == 1.0


def test_quality_gate_reports_missing_event_and_unbound_shots() -> None:
    plan = _plan()
    plan.scenes[1].source_event_ids = []
    episode = Episode(
        episode_number=1,
        narrative_plan=plan,
        shots=[Shot(shot_number=1, scene_description="空泛概括", emotion="紧张")],
    )

    report = evaluate_narrative_quality(episode, _analysis())

    assert report.passed is False
    assert report.important_event_coverage.score == 0
    assert report.shot_binding_coverage.score == 0
    assert report.failures
