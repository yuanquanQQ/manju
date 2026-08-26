from app.agents.director import _parse_shots


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
