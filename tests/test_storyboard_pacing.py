from dataclasses import dataclass

from app.pipeline.pacing import normalize_episode_duration, pacing_target


@dataclass
class _Video:
    duration_seconds: float


@dataclass
class _Shot:
    duration_seconds: float
    video_generation: _Video


def test_long_chapter_targets_dense_one_minute_storyboard() -> None:
    target = pacing_target(3400, event_count=3, dialogue_count=4)

    assert target.min_shots >= 18
    assert 20 <= target.target_shots <= 26
    assert target.min_duration_seconds == 60.0
    assert target.target_duration_seconds >= 65.0


def test_duration_normalizer_keeps_shot_and_video_duration_in_sync() -> None:
    shots = [_Shot(3.0, _Video(3.0)) for _ in range(18)]

    total = normalize_episode_duration(shots)

    assert total >= 60.0
    assert all(shot.duration_seconds <= 5.0 for shot in shots)
    assert all(
        shot.video_generation.duration_seconds == shot.duration_seconds
        for shot in shots
    )


def test_story_plan_controls_pacing_instead_of_chapter_length() -> None:
    target = pacing_target(
        9000,
        event_count=12,
        dialogue_count=20,
        planned_shots=14,
        planned_duration_seconds=56,
    )

    assert target.target_shots == 14
    assert target.target_duration_seconds == 56
    assert target.min_duration_seconds == 47.6
