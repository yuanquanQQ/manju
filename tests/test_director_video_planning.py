import pytest

from app.agents.director import (
    _is_usable_character_profile,
    _parse_shots,
    _validate_compact_items,
    _validate_dialogues_against_source,
)


def test_character_profile_quality_rejects_legacy_placeholders() -> None:
    assert not _is_usable_character_profile("已锁定定妆的秦风")
    assert not _is_usable_character_profile("十八岁黑发少年")
    assert _is_usable_character_profile(
        "十八岁男性，清瘦长脸，狭长深棕眼，鼻梁挺直，下颌利落，冷白肤色，身形修长；"
        "黑发高束成窄马尾，穿墨青交领窄袖袍，深灰麻质护腕与黑色短靴，腰侧佩一枚缺角青玉药牌作为唯一标志。"
    )


def test_compact_shot_rejects_character_without_cast_profile() -> None:
    errors = _validate_compact_items(
        [
            {
                "scene_description": "秦风与陌生人站在药圃入口对峙",
                "characters": ["秦风", "陌生人"],
                "camera_angle": "medium shot",
                "camera_movement": "static",
                "visual_prompt": "Qin Feng confronts a stranger at the garden gate",
            }
        ],
        {"秦风": "合格定妆"},
    )

    assert "陌生人" in "；".join(errors)


def test_director_backfills_image_and_video_generation_fields() -> None:
    shots = _parse_shots(
        {
            "shots": [
                {
                    "shot_number": 1,
                    "scene_description": "少年在晨雾中缓慢抬头",
                    "environment": {
                        "layout": "少年位于药圃中景",
                        "lighting": "清晨冷色侧光",
                        "atmosphere": "晨雾从左向右缓慢流动",
                    },
                    "characters": [
                        {
                            "name": "秦风",
                            "appearance": "十八岁黑发少年",
                            "pose": "缓慢抬头",
                            "expression": "目光逐渐坚定",
                        }
                    ],
                    "camera_movement": "dolly",
                    "transition": "dissolve",
                    "duration_seconds": 4,
                }
            ]
        }
    )

    assert len(shots) == 1
    shot = shots[0]
    assert shot.image_prompt.startswith("masterpiece, best quality")
    assert shot.video_generation.engine_profile == "minimax_h3_fl2va"
    assert "秦风缓慢抬头" in shot.video_generation.subject_motion
    assert "晨雾" in shot.video_generation.environment_motion
    assert shot.video_generation.camera_movement == "slow_push"
    assert shot.video_generation.transition_out == "dissolve"
    assert "face morphing" in shot.video_generation.negative_prompt
    assert shot.video_generation.motion_prompt.startswith("节拍划分")
    assert "拍1｜本镜0-0.5秒｜建立拍" in shot.video_generation.motion_prompt
    assert "执行锁定：" in shot.video_generation.motion_prompt
    assert "首帧承接：" in shot.video_generation.motion_prompt
    assert "尾帧定格：C01" in shot.video_generation.motion_prompt


def test_director_binds_exact_dialogue_to_audio_and_lip_sync() -> None:
    shots = _parse_shots(
        {
            "shots": [
                {
                    "shot_number": 1,
                    "scene_description": "秦风站在药圃左侧看向来人",
                    "characters": [
                        {"name": "秦风", "appearance": "十八岁黑发青年"}
                    ],
                    "dialogue": "秦风：秦三秋！",
                    "duration_seconds": 3,
                }
            ]
        }
    )

    shot = shots[0]
    assert shot.audio_generation.mode == "dialogue"
    assert shot.audio_generation.speaker == "秦风"
    assert shot.audio_generation.text == "秦三秋！"
    assert shot.lip_sync.enabled is True
    assert shot.lip_sync.target_character == "秦风"


def test_director_rejects_multiple_speakers_in_one_shot() -> None:
    with pytest.raises(ValueError, match="每个镜头只能包含一个说话人"):
        _parse_shots(
            {
                "shots": [
                    {
                        "scene_description": "两人在药圃入口先后回应",
                        "dialogue": "秦风：秦三秋！／秦三秋：属下在。",
                    }
                ]
            }
        )


def test_dialogue_validation_requires_literal_source_text() -> None:
    source = "秦三秋说道：少爷！这里的情况，必须马上奏报上去。"
    assert not _validate_dialogues_against_source(
        [{"dialogue": "秦三秋：少爷！这里的情况，必须马上奏报上去。", "characters": ["秦三秋"]}],
        source,
    )
    assert _validate_dialogues_against_source(
        [{"dialogue": "秦三秋：必须马上上报。", "characters": ["秦三秋"]}],
        source,
    )


def test_dialogue_validation_requires_original_speaker() -> None:
    source = "秦风喝道：“秦三秋！”"
    errors = _validate_dialogues_against_source(
        [{"dialogue": "林浪：秦三秋！", "characters": ["林浪"]}],
        source,
        {("秦风", "秦三秋！")},
    )

    assert any("说话人林浪" in error for error in errors)


def test_dialogue_validation_accepts_source_anchored_adaptation() -> None:
    errors = _validate_dialogues_against_source(
        [{"dialogue": "秦风：这座铁矿，我收下了。", "characters": ["秦风"]}],
        "林家铁矿，我要定了！",
        {("秦风", "林家铁矿，我要定了！")},
        {("秦风", "这座铁矿，我收下了。")},
    )

    assert errors == []
