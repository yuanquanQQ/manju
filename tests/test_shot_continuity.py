from app.pipeline.continuity import plan_episode_continuity


def test_continuity_resumes_live_scene_after_flashback() -> None:
    episode = {
        "character_profiles": {"秦风": "少年", "林淑婉": "少女"},
        "shots": [
            {
                "shot_number": 1,
                "scene_description": "清晨药圃，秦风查看枯萎灵草",
                "video_generation": {"subject_motion": "秦风俯身查看灵草"},
            },
            {
                "shot_number": 2,
                "scene_description": "秦风发现草根有移植痕迹",
                "video_generation": {"subject_motion": "秦风抬眼看向来路"},
            },
            {
                "shot_number": 3,
                "scene_description": "秦风回忆前世被一剑刺穿的瞬间",
                "video_generation": {"subject_motion": "剑锋停在胸前"},
            },
            {
                "shot_number": 4,
                "scene_description": "回到药圃，秦风与林浪签下赌约",
                "video_generation": {"subject_motion": "秦风提笔落款"},
            },
        ]
    }

    stats = plan_episode_continuity(episode, force=True)
    plans = [shot["continuity_plan"] for shot in episode["shots"]]

    assert stats == {"plans_updated": 4, "reference_links": 2, "groups": 2}
    assert plans[0]["group_id"] == "scene_01"
    assert plans[1]["reference_shot_number"] == 1
    assert plans[2]["group_id"] == "flashback_01"
    assert plans[2]["reference_shot_number"] == 0
    assert plans[3]["group_id"] == "scene_01"
    assert plans[3]["reference_shot_number"] == 2
    assert "not a posed portrait" in plans[3]["keyframe_prompt"]


def test_continuity_resets_image_reference_when_cast_changes() -> None:
    episode = {
        "character_profiles": {"秦风": "少年", "秦三秋": "护卫"},
        "shots": [
            {"shot_number": 1, "scene_description": "秦风走入药圃"},
            {"shot_number": 2, "scene_description": "秦风俯身检查灵草"},
            {"shot_number": 3, "scene_description": "秦三秋跑来向秦风禀报"},
        ],
    }

    stats = plan_episode_continuity(episode, force=True)
    plans = [shot["continuity_plan"] for shot in episode["shots"]]

    assert stats["reference_links"] == 1
    assert plans[1]["reference_shot_number"] == 1
    assert plans[2]["reference_mode"] == "none"
    assert plans[2]["reference_shot_number"] == 0


def test_continuity_resets_reference_for_detail_insert() -> None:
    episode = {
        "character_profiles": {"Qin Feng": "young male hero"},
        "shots": [
            {
                "shot_number": 1,
                "scene_description": "Qin Feng enters the herb garden",
                "characters": [{"name": "Qin Feng"}],
                "camera_angle": "wide shot",
            },
            {
                "shot_number": 2,
                "scene_description": "Qin Feng checks a damaged spirit herb",
                "characters": [{"name": "Qin Feng"}],
                "camera_angle": "macro insert",
                "image_prompt": "macro insert of exposed roots and one cyan sleeve",
            },
            {
                "shot_number": 3,
                "scene_description": "Qin Feng kneels beside the damaged field",
                "characters": [{"name": "Qin Feng"}],
                "camera_angle": "medium shot",
            },
        ],
    }

    plan_episode_continuity(episode, force=True)

    plan = episode["shots"][1]["continuity_plan"]
    assert plan["reference_mode"] == "none"
    assert plan["reference_shot_number"] == 0
    next_plan = episode["shots"][2]["continuity_plan"]
    assert next_plan["reference_mode"] == "none"
    assert next_plan["reference_shot_number"] == 0


def test_continuity_preserves_reviewed_values_without_force() -> None:
    episode = {
        "shots": [
            {
                "shot_number": 1,
                "scene_description": "山门前对峙",
                "continuity_plan": {
                    "group_id": "reviewed_scene",
                    "match_anchor": "保留石狮和逆光",
                },
            }
        ]
    }

    plan_episode_continuity(episode)

    plan = episode["shots"][0]["continuity_plan"]
    assert plan["group_id"] == "reviewed_scene"
    assert plan["match_anchor"] == "保留石狮和逆光"


def test_continuity_plans_match_cut_and_scene_change_dissolve() -> None:
    episode = {
        "character_profiles": {"秦风": "青衣少年"},
        "shots": [
            {
                "shot_number": 1,
                "scene_description": "秦风沿药圃小径向右走",
                "characters": [{"name": "秦风"}],
                "video_generation": {"subject_motion": "秦风向右迈步"},
            },
            {
                "shot_number": 2,
                "scene_description": "秦风蹲下查看灵药",
                "characters": [{"name": "秦风"}],
                "video_generation": {"subject_motion": "秦风顺势蹲下"},
            },
            {
                "shot_number": 3,
                "scene_description": "秦风回忆前世被剑刺穿",
                "characters": [{"name": "秦风"}],
                "video_generation": {"subject_motion": "秦风身体后仰"},
            },
        ],
    }

    plan_episode_continuity(episode, force=True)

    first_video = episode["shots"][0]["video_generation"]
    second_video = episode["shots"][1]["video_generation"]
    assert first_video["transition_out"] == "match_cut"
    assert first_video["transition_frames"] == 4
    assert second_video["transition_out"] == "dissolve"
    assert second_video["transition_frames"] == 8
    assert "直接接入" in episode["shots"][0]["continuity_plan"]["match_action"]
