from app.pipeline.audio_timing import (
    estimate_speech_duration,
    optimize_episode_audio_timing,
    split_spoken_text,
)


def test_speech_estimator_respects_rate_and_punctuation() -> None:
    text = "秦风：你先别急，我马上回来。"

    normal = estimate_speech_duration(text, "+0%")
    fast = estimate_speech_duration(text, "+30%")

    assert normal > fast > 0


def test_long_dialogue_is_split_and_marks_video_for_planning() -> None:
    episode = {
        "shots": [
            {
                "shot_number": 1,
                "scene_description": "秦风看向林浪。",
                "duration_seconds": 3.0,
                "video_generation": {"duration_seconds": 3.0},
                "audio_generation": {
                    "mode": "dialogue",
                    "speaker": "秦风",
                    "text": (
                        "我知道你现在不相信我，但是山门外的追兵马上就到，"
                        "我们必须立刻离开这里，否则所有人都会被困住。"
                    ),
                    "rate": "+0%",
                },
            }
        ]
    }

    summary = optimize_episode_audio_timing(
        episode,
        minimum_episode_seconds=0,
    )
    shot = episode["shots"][0]
    audio = shot["audio_generation"]

    assert summary.needs_split_shots == 1
    assert audio["timing_status"] == "needs_split"
    assert audio["recommended_segments"] >= 2
    assert len(audio["segments"]) >= 2
    assert shot["duration_seconds"] == shot["video_generation"]["duration_seconds"]
    assert shot["duration_seconds"] > 3.0
    assert shot["lip_sync"]["enabled"] is True
    assert shot["lip_sync"]["target_character"] == "秦风"


def test_episode_timing_reaches_one_minute_when_shots_have_room() -> None:
    episode = {
        "shots": [
            {
                "shot_number": index,
                "scene_description": "人物短暂观察四周。",
                "duration_seconds": 3.0,
                "video_generation": {"duration_seconds": 3.0},
                "audio_generation": {"mode": "mute"},
            }
            for index in range(1, 19)
        ]
    }

    summary = optimize_episode_audio_timing(episode)

    assert summary.total_duration_seconds >= 60.0
    assert all(
        shot["duration_seconds"] == shot["video_generation"]["duration_seconds"]
        for shot in episode["shots"]
    )


def test_split_spoken_text_removes_speaker_prefix() -> None:
    parts = split_spoken_text(
        "林浪：第一句话说完。第二句话还要继续，而且内容比较长。",
        max_chars=12,
    )

    assert len(parts) >= 2
    assert not parts[0].startswith("林浪：")
