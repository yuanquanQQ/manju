from __future__ import annotations

import json

import pytest

from app.agents.director import _expand_compact_beat
from app.agents.story_planner import (
    StoryPlanningError,
    format_episode_plan,
    plan_episode,
)
from app.domain.novel import (
    AnalysisProvenance,
    ChapterAnalysis,
    Dialogue,
    EvidenceSpan,
    NarrativeEvent,
)
from app.services.desktop_service import DesktopProjectService


class _PlannerLLM:
    model_name = "planner-test"

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = 0

    def complete(self, **_kwargs) -> dict:
        self.calls += 1
        return self.response


def _analysis() -> ChapterAnalysis:
    return ChapterAnalysis(
        chapter_id="ch_000001",
        events=[
            NarrativeEvent(
                event_id="event_critical",
                sequence_index=0,
                summary="秦风发现药圃被人破坏",
                participants=["秦风"],
                importance=5,
                result="秦风确认有人设局",
                evidence=EvidenceSpan(
                    chapter_id="ch_000001",
                    start=0,
                    end=8,
                    quote="秦风发现药圃",
                ),
                confidence=0.95,
            ),
            NarrativeEvent(
                event_id="event_bet",
                sequence_index=1,
                summary="秦风接受林浪的赌约",
                participants=["秦风", "林浪"],
                importance=4,
                result="铁矿成为赌注",
                evidence=EvidenceSpan(
                    chapter_id="ch_000001",
                    start=9,
                    end=17,
                    quote="秦风接受赌约",
                ),
                confidence=0.95,
            ),
        ],
        dialogues=[
            Dialogue(
                dialogue_id="dialogue_bet",
                speaker="秦风",
                text="林家铁矿，我要定了！",
                emotion="从容",
                evidence=EvidenceSpan(
                    chapter_id="ch_000001",
                    start=18,
                    end=29,
                    quote="林家铁矿，我要定了！",
                ),
                confidence=0.98,
            )
        ],
        summary="秦风识破药圃阴谋并接受赌约。",
        provenance=AnalysisProvenance(
            model="extractor-test",
            prompt_version="chapter-analysis-v1",
            input_hash="source-hash",
            chunk_count=1,
        ),
    )


def _response() -> dict:
    return {
        "episode_title": "药圃赌局",
        "logline": "重生后的秦风识破药圃阴谋，并借敌人的轻视反设赌局。",
        "protagonist": "秦风",
        "episode_goal": "保住药圃并夺回主动权",
        "central_conflict": "秦风必须在林浪的挑衅与药圃危机之间反客为主",
        "stakes": "失败将失去药圃并断绝疗伤机会",
        "opening_hook": "秦风在枯死的灵药前意识到自己回到了悲剧起点",
        "scenes": [
            {
                "scene_number": 1,
                "title": "枯死的药圃",
                "source_event_ids": ["event_critical"],
                "purpose": "建立生存危机并证明秦风拥有前世知识",
                "location": "秦家药圃",
                "pov_character": "秦风",
                "goal": "找出灵药枯萎原因",
                "obstacle": "破坏痕迹被伪装成管理失误",
                "stakes": "秦风无法取得疗伤灵药",
                "strategy": "检查草根、灵泉和离阳草位置",
                "turn": "秦风确认这不是意外而是人为布局",
                "outcome": "秦风掌握反击依据",
                "value_before": "被动受害",
                "value_after": "掌握真相",
                "emotion_start": "困惑",
                "emotion_end": "警觉",
                "revelation": "离阳草是药圃灾害根源",
                "setup_or_payoff": "铺设秦风炼丹知识优势",
                "visual_motif": "枯黄药叶与红色离阳草对照",
                "dialogues": [],
                "estimated_shots": 6,
                "estimated_duration_seconds": 22,
            },
            {
                "scene_number": 2,
                "title": "反设赌局",
                "source_event_ids": ["event_bet"],
                "purpose": "让主角由防守转为主动进攻",
                "location": "药圃谷口",
                "pov_character": "秦风",
                "goal": "诱使林浪押上真正有价值的筹码",
                "obstacle": "林浪试图以退婚和旧伤激怒秦风",
                "stakes": "失控会让秦风重走前世覆辙",
                "strategy": "故作无知并接受对方提出的赌约",
                "turn": "林浪主动押上林家铁矿",
                "outcome": "秦风取得复仇的第一枚筹码",
                "value_before": "受辱受压",
                "value_after": "反客为主",
                "emotion_start": "克制",
                "emotion_end": "轻蔑笃定",
                "revelation": "秦风不会再被相同手段激怒",
                "setup_or_payoff": "开启铁矿赌约线",
                "visual_motif": "剑锋包围与赌约墨迹",
                "dialogues": [
                    {
                        "source_dialogue_id": "dialogue_bet",
                        "speaker": "秦风",
                        "source_text": "林家铁矿，我要定了！",
                        "adapted_text": "这座铁矿，我收下了。",
                        "intent": "公开接下赌约并压制林浪",
                        "subtext": "秦风已经知道结局",
                        "adaptation_reason": "压缩为更适合表演的短句",
                    }
                ],
                "estimated_shots": 8,
                "estimated_duration_seconds": 34,
            },
        ],
        "climax": "林浪押上铁矿，秦风当场接下赌约",
        "cliffhanger": "林浪以为秦风入局，秦风却露出早已胜券在握的笑",
        "emotional_arc": ["危机", "识破", "受压", "反制", "胜券在握"],
        "character_arcs": [
            {
                "character": "秦风",
                "external_goal": "保住药圃",
                "inner_need": "摆脱前世受害者心态",
                "pressure": "林浪重复前世的羞辱与刺激",
                "choice": "克制愤怒并主动设局",
                "change": "从被动承受转为主动复仇",
            }
        ],
        "thread_updates": [
            {
                "thread": "药圃赌约",
                "previous_state": "",
                "new_state": "秦风与林浪以药圃和铁矿立赌",
                "status": "opened",
            }
        ],
        "unresolved_threads": ["秦风如何在一个月内恢复药圃"],
    }


def test_plan_episode_builds_dramatic_plan_with_source_anchors() -> None:
    llm = _PlannerLLM(_response())
    source = "秦风发现药圃。秦风接受赌约。林家铁矿，我要定了！"

    plan = plan_episode(
        _analysis(),
        source,
        llm=llm,
        episode_number=1,
        episode_title="重生十万年",
        source_chapter_ids=["ch_000001", "ch_000002"],
    )

    assert plan.target_shot_count == 14
    assert plan.target_duration_seconds == 56
    assert plan.provenance.input_hash == "source-hash"
    assert plan.source_chapter_ids == ["ch_000001", "ch_000002"]
    assert plan.scenes[1].value_before != plan.scenes[1].value_after
    assert "目标：找出灵药枯萎原因" in format_episode_plan(plan)


def test_plan_episode_retries_then_fails_when_important_event_is_missing() -> None:
    response = _response()
    response["scenes"][0]["source_event_ids"] = []
    llm = _PlannerLLM(response)

    with pytest.raises(StoryPlanningError, match="重要事件未进入场次"):
        plan_episode(
            _analysis(),
            "秦风发现药圃。秦风接受赌约。林家铁矿，我要定了！",
            llm=llm,
        )

    assert llm.calls == 2


def test_desktop_editor_updates_embedded_and_standalone_story_plan(tmp_path) -> None:
    plan = plan_episode(
        _analysis(),
        "秦风发现药圃。秦风接受赌约。林家铁矿，我要定了！",
        llm=_PlannerLLM(_response()),
    )
    project = tmp_path / "projects" / "demo"
    episode_path = project / "production" / "episodes" / "episode_001.json"
    plan_path = project / "production" / "story_plans" / "episode_001.json"
    episode_path.parent.mkdir(parents=True)
    plan_path.parent.mkdir(parents=True)
    payload = plan.model_dump(mode="json")
    plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    episode_path.write_text(
        json.dumps(
            {
                "episode_number": 1,
                "episode_title": "药圃赌局",
                "narrative_plan": payload,
                "shots": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = DesktopProjectService(tmp_path / "projects")

    service.save_story_plan_summary(
        "demo",
        1,
        episode_goal="诱使林浪主动押上铁矿",
        central_conflict="秦风必须压住旧恨，利用林浪的傲慢反设赌局",
        climax="林浪押上铁矿后，秦风立即接下赌约",
        cliffhanger="秦风暗示药圃危机正是夺矿的第一步",
    )

    standalone = json.loads(plan_path.read_text(encoding="utf-8"))
    embedded = json.loads(episode_path.read_text(encoding="utf-8"))[
        "narrative_plan"
    ]
    assert standalone["episode_goal"] == "诱使林浪主动押上铁矿"
    assert embedded == standalone


def test_director_binding_inherits_dramatic_scene_contract() -> None:
    plan = plan_episode(
        _analysis(),
        "秦风发现药圃。秦风接受赌约。林家铁矿，我要定了！",
        llm=_PlannerLLM(_response()),
    )

    shot = _expand_compact_beat(
        {
            "planned_scene_number": 2,
            "scene_description": "秦风平静看着林浪在赌约上落笔",
            "characters": ["秦风"],
            "visible_action": "秦风按住赌约，抬眼看向林浪",
            "visual_prompt": "Qin Feng calmly claims the signed wager",
        },
        profiles={"秦风": "清瘦长脸的黑发少年，身穿墨青窄袖长袍，腰悬缺角青玉药牌"},
        episode_plan=plan,
    )

    binding = shot["narrative_binding"]
    assert binding["scene_number"] == 2
    assert binding["dramatic_purpose"] == plan.scenes[1].purpose
    assert binding["source_event_ids"] == ["event_bet"]
    assert binding["value_before"] == "受辱受压"
    assert binding["value_after"] == "反客为主"
