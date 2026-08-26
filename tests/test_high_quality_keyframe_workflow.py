from scripts.generate_high_quality_keyframe import build_shot_prompt
from workflows.high_quality_image.generate_keyframe import (
    build_end_frame_prompt,
    build_identity_edit_prompt,
)


def test_start_keyframe_prompt_separates_composition_and_cast_identity() -> None:
    prompt = build_identity_edit_prompt("秦风与林浪对峙", 2)

    assert "图1的场景构图" in prompt
    assert "图2、图3" in prompt
    assert "不得把不同人物的五官或服装互相融合" in prompt
    assert "秦风与林浪对峙" in prompt


def test_end_keyframe_prompt_forbids_new_scene_and_large_motion() -> None:
    prompt = build_end_frame_prompt("秦风收回右手，目光落向药圃")

    assert "只编辑人物动作" in prompt
    assert "不得重新造景" in prompt
    assert "单一动作" in prompt
    assert "秦风收回右手" in prompt


def test_shot_prompt_keeps_episode_continuity_and_story_action() -> None:
    shot = {
        "scene_description": "秦风在药圃蹲下查看断茎",
        "image_prompt": "清晨逆光，中近景",
        "continuity_plan": {"keyframe_prompt": "延续前镜头从左向右的视线"},
        "video_generation": {"continuity_constraints": "深青外袍保持一致"},
    }

    prompt = build_shot_prompt(shot, "start")

    assert "清晨逆光" in prompt
    assert "延续前镜头" in prompt
    assert "深青外袍保持一致" in prompt
    assert "不是人物摆拍" in prompt
