from __future__ import annotations

import pytest

from app.agents.adaptation_planner import (
    AdaptationPlanningError,
    plan_adaptation_outline,
)
from app.compiler.adaptation_material import combine_adaptation_material
from app.domain.narrative import (
    CharacterArcBeat,
    EpisodePlan,
    NarrativePlanProvenance,
    ScenePlan,
    StoryThreadUpdate,
)
from app.domain.novel import (
    AnalysisProvenance,
    ChapterAnalysis,
    EvidenceSpan,
    NarrativeEvent,
    StandardChapter,
)
from app.pipeline.story_bible import evolve_story_bible, rebuild_story_bible
from app.pipeline.storyboard import _plan_outline_incrementally


class _OutlineLLM:
    model_name = "outline-test"

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = 0

    def complete(self, **_kwargs) -> dict:
        self.calls += 1
        return self.response


def _chapter(order: int) -> StandardChapter:
    return StandardChapter(
        chapter_id=f"ch_{order:06d}",
        order=order,
        title=f"第{order}章",
        content=f"第{order}章正文",
        source_document_id="document",
        source_file="novel.txt",
        source_start=order * 10,
        source_end=order * 10 + 6,
        content_hash=f"hash-{order}",
    )


def _analysis(order: int) -> ChapterAnalysis:
    chapter_id = f"ch_{order:06d}"
    return ChapterAnalysis(
        chapter_id=chapter_id,
        events=[
            NarrativeEvent(
                event_id=f"event-{order}",
                sequence_index=0,
                summary=f"第{order}章关键事件",
                importance=5,
                evidence=EvidenceSpan(
                    chapter_id=chapter_id,
                    start=0,
                    end=6,
                    quote=f"第{order}章正文",
                ),
                confidence=0.9,
            )
        ],
        summary=f"第{order}章摘要",
        provenance=AnalysisProvenance(
            model="extractor",
            prompt_version="chapter-analysis-v1",
            input_hash=f"hash-{order}",
            chunk_count=1,
        ),
    )


def _outline_response() -> dict:
    return {
        "groups": [
            {
                "episode_number": 1,
                "episode_title": "两章合流",
                "chapter_ids": ["ch_000001", "ch_000002"],
                "boundary_reason": "第二章完成第一章危机的反转并形成新悬念",
                "opening_hook": "第一章危机突然出现",
                "closing_hook": "第二章揭示更大的敌人",
            }
        ]
    }


def test_outline_can_combine_consecutive_chapters_into_one_episode() -> None:
    outline = plan_adaptation_outline(
        [_chapter(1), _chapter(2)],
        [_analysis(1), _analysis(2)],
        llm=_OutlineLLM(_outline_response()),
    )

    assert len(outline.groups) == 1
    assert outline.groups[0].chapter_ids == ["ch_000001", "ch_000002"]


def test_outline_rejects_missing_or_reordered_chapters() -> None:
    response = _outline_response()
    response["groups"][0]["chapter_ids"] = ["ch_000002", "ch_000001"]
    llm = _OutlineLLM(response)

    with pytest.raises(AdaptationPlanningError, match="完整覆盖"):
        plan_adaptation_outline(
            [_chapter(1), _chapter(2)],
            [_analysis(1), _analysis(2)],
            llm=llm,
        )

    assert llm.calls == 2


def test_combined_material_preserves_all_source_chapter_ids_and_events() -> None:
    material = combine_adaptation_material(
        [_chapter(1), _chapter(2)],
        [_analysis(1), _analysis(2)],
    )

    assert material.chapter_ids == ["ch_000001", "ch_000002"]
    assert [event.event_id for event in material.analysis.events] == [
        "event-1",
        "event-2",
    ]
    assert "【第1章】" in material.source_text
    assert material.analysis.provenance.chunk_count == 2


def test_story_bible_accumulates_character_arc_and_open_threads() -> None:
    scene = ScenePlan(
        scene_number=1,
        title="转折",
        purpose="迫使主角主动选择",
        location="药圃",
        goal="守住药圃",
        obstacle="敌人逼迫",
        stakes="失去疗伤机会",
        strategy="反设赌局",
        turn="敌人押上铁矿",
        outcome="主角取得主动",
        value_before="被动",
        value_after="主动",
        emotion_start="压抑",
        emotion_end="笃定",
        estimated_shots=6,
        estimated_duration_seconds=24,
    )
    plan = EpisodePlan(
        episode_number=1,
        episode_title="赌局",
        source_chapter_ids=["ch_000001", "ch_000002"],
        logline="秦风利用敌人的傲慢反设赌局夺取主动。",
        protagonist="秦风",
        episode_goal="夺回主动",
        central_conflict="秦风必须克制旧恨并诱敌入局",
        stakes="失败将失去药圃",
        opening_hook="药圃濒临毁灭",
        scenes=[scene, scene.model_copy(update={"scene_number": 2, "title": "落子"})],
        climax="敌人押上铁矿",
        cliffhanger="秦风显露早有准备",
        emotional_arc=["受压", "反制"],
        character_arcs=[
            CharacterArcBeat(
                character="秦风",
                external_goal="保住药圃",
                inner_need="摆脱受害者心态",
                pressure="敌人重复前世羞辱",
                choice="克制并设局",
                change="从被动承受到主动反击",
            )
        ],
        thread_updates=[
            StoryThreadUpdate(
                thread="药圃赌约",
                new_state="赌约已经成立",
                status="opened",
            )
        ],
        unresolved_threads=["一个月内如何恢复药圃"],
        provenance=NarrativePlanProvenance(
            model="planner",
            prompt_version="episode-story-plan-v1",
            input_hash="combined",
        ),
    )

    bible = evolve_story_bible(None, plan)

    assert bible.source_chapter_ids == ["ch_000001", "ch_000002"]
    assert bible.characters["秦风"].arc_state == "从被动承受到主动反击"
    assert bible.threads["药圃赌约"].status == "opened"
    assert bible.threads["一个月内如何恢复药圃"].status == "held"


def test_incremental_outline_preserves_existing_prefix_and_extends(tmp_path) -> None:
    existing = plan_adaptation_outline(
        [_chapter(1), _chapter(2)],
        [_analysis(1), _analysis(2)],
        llm=_OutlineLLM(_outline_response()),
    )
    outline_path = tmp_path / "adaptation_outline.json"
    outline_path.write_text(existing.model_dump_json(), encoding="utf-8")

    outline = _plan_outline_incrementally(
        outline_path,
        [_chapter(1), _chapter(2), _chapter(3)],
        [_analysis(1), _analysis(2), _analysis(3)],
        _OutlineLLM({"groups": []}),
    )

    assert outline.groups[0] == existing.groups[0]
    assert outline.groups[1].chapter_ids == ["ch_000003"]
    assert outline.groups[1].episode_number == 2


def test_story_bible_rebuild_orders_plans_and_preserves_world_rules() -> None:
    first = _story_plan(1, "第一步")
    second = _story_plan(2, "第二步")

    bible = rebuild_story_bible(
        [second, first],
        world_rules=["灵力不可凭空恢复"],
    )

    assert bible.last_episode_number == 2
    assert bible.characters["秦风"].arc_state == "第二步"
    assert bible.world_rules == ["灵力不可凭空恢复"]


def _story_plan(episode_number: int, change: str) -> EpisodePlan:
    scene = ScenePlan(
        scene_number=1,
        title="推进",
        purpose="迫使主角承担新的代价",
        location="山道",
        goal="赶到山门",
        obstacle="追兵封路",
        stakes="同伴会被抓走",
        strategy="制造声东击西",
        turn="同伴主动留下断后",
        outcome="主角成功脱身",
        value_before="同行",
        value_after="分离",
        emotion_start="焦急",
        emotion_end="悲壮",
        estimated_shots=3,
        estimated_duration_seconds=15,
    )
    return EpisodePlan(
        episode_number=episode_number,
        episode_title=f"第{episode_number}集",
        source_chapter_ids=[f"ch_{episode_number:06d}"],
        logline="秦风突破追兵封锁并承担同伴留下的代价。",
        protagonist="秦风",
        episode_goal="突破封锁",
        central_conflict="追兵切断山路",
        stakes="同伴会被抓走",
        opening_hook="追兵突然封路",
        scenes=[scene, scene.model_copy(update={"scene_number": 2, "title": "分离"})],
        climax="同伴主动留下断后",
        cliffhanger="山门内还有埋伏",
        emotional_arc=["焦急", "悲壮"],
        character_arcs=[
            CharacterArcBeat(
                character="秦风",
                external_goal="突破封锁",
                pressure="同伴面临被捕",
                choice="接受同伴留下",
                change=change,
            )
        ],
        provenance=NarrativePlanProvenance(
            model="test",
            prompt_version="episode-story-plan-v1",
            input_hash=f"hash-{episode_number}",
        ),
    )
