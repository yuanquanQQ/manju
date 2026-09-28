from __future__ import annotations

import json

import pytest

from app.domain.narrative import EpisodePlan, NarrativePlanProvenance, ScenePlan
from app.pipeline.story_plan_store import load_episode_plan, save_episode_plan


def _plan() -> EpisodePlan:
    scene = ScenePlan(
        scene_number=1,
        title="选择",
        purpose="迫使主角明确立场",
        location="庭院",
        goal="保护证人",
        obstacle="追兵已经到达",
        stakes="证人会被灭口",
        strategy="故意暴露假路线",
        turn="证人选择留下作证",
        outcome="二人结成同盟",
        value_before="互不信任",
        value_after="共同承担",
        emotion_start="猜疑",
        emotion_end="决绝",
        estimated_shots=3,
        estimated_duration_seconds=15,
    )
    return EpisodePlan(
        episode_number=1,
        episode_title="证人",
        source_chapter_ids=["ch_000001"],
        logline="秦风冒险保护证人，并换来对方真正的信任。",
        protagonist="秦风",
        episode_goal="保护证人",
        central_conflict="追兵封锁庭院",
        stakes="证人会被灭口",
        opening_hook="追兵撞开院门",
        scenes=[scene, scene.model_copy(update={"scene_number": 2, "title": "同盟"})],
        climax="证人主动留下作证",
        cliffhanger="幕后主使亲自现身",
        emotional_arc=["猜疑", "决绝"],
        provenance=NarrativePlanProvenance(
            model="test",
            prompt_version="episode-story-plan-v1",
            input_hash="hash",
        ),
    )


def test_store_validates_and_synchronizes_standalone_and_embedded_plan(tmp_path) -> None:
    episode_path = tmp_path / "production" / "episodes" / "episode_001.json"
    episode_path.parent.mkdir(parents=True)
    episode_path.write_text('{"episode_number": 1, "shots": []}', encoding="utf-8")

    plan_path = save_episode_plan(tmp_path, 1, _plan())

    standalone = json.loads(plan_path.read_text(encoding="utf-8"))
    embedded = json.loads(episode_path.read_text(encoding="utf-8"))["narrative_plan"]
    assert embedded == standalone
    assert load_episode_plan(tmp_path, 1).episode_title == "证人"


def test_store_rejects_episode_number_mismatch(tmp_path) -> None:
    with pytest.raises(ValueError, match="集号"):
        save_episode_plan(tmp_path, 2, _plan())


def test_store_revalidates_model_copy_updates(tmp_path) -> None:
    invalid = _plan().model_copy(update={"episode_goal": ""})

    with pytest.raises(ValueError, match="episode_goal"):
        save_episode_plan(tmp_path, 1, invalid)
