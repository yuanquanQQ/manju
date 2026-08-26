"""Episode pacing targets and post-generation validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class _TimedShot(Protocol):
    duration_seconds: float
    video_generation: object


@dataclass(frozen=True, slots=True)
class EpisodePacingTarget:
    min_shots: int
    target_shots: int
    max_shots: int
    min_duration_seconds: float
    target_duration_seconds: float


def pacing_target(
    character_count: int,
    *,
    event_count: int = 0,
    dialogue_count: int = 0,
) -> EpisodePacingTarget:
    """Choose a dense short-drama target while keeping generation affordable."""

    source_target = 18 + max(0, character_count - 1800) // 350
    narrative_target = 12 + event_count * 2 + min(dialogue_count, 6)
    target_shots = max(20, min(26, max(source_target, narrative_target)))
    target_duration = max(65.0, min(90.0, round(character_count / 48)))
    return EpisodePacingTarget(
        min_shots=max(18, target_shots - 3),
        target_shots=target_shots,
        max_shots=min(28, target_shots + 3),
        min_duration_seconds=60.0,
        target_duration_seconds=target_duration,
    )


def normalize_episode_duration(
    shots: list[_TimedShot],
    *,
    minimum_seconds: float = 60.0,
    maximum_shot_seconds: float = 5.0,
) -> float:
    """Scale editable shot durations so a valid dense board reaches one minute."""

    if not shots:
        return 0.0
    current = sum(float(shot.duration_seconds) for shot in shots)
    if current >= minimum_seconds:
        return current
    remaining = minimum_seconds - current
    eligible = list(shots)
    while remaining > 0.001 and eligible:
        share = remaining / len(eligible)
        next_eligible: list[_TimedShot] = []
        distributed = 0.0
        for shot in eligible:
            original = float(shot.duration_seconds)
            available = maximum_shot_seconds - original
            addition = max(0.0, min(share, available))
            if addition:
                shot.duration_seconds = round(
                    original + addition,
                    2,
                )
                video = getattr(shot, "video_generation", None)
                if video is not None and hasattr(video, "duration_seconds"):
                    video.duration_seconds = shot.duration_seconds
                distributed += float(shot.duration_seconds) - original
            if available - addition > 0.001:
                next_eligible.append(shot)
        if distributed <= 0.001:
            break
        remaining -= distributed
        eligible = next_eligible
    total = sum(float(shot.duration_seconds) for shot in shots)
    while total < minimum_seconds - 0.001:
        changed = False
        for shot in shots:
            if float(shot.duration_seconds) + 0.01 <= maximum_shot_seconds:
                shot.duration_seconds = round(
                    float(shot.duration_seconds) + 0.01,
                    2,
                )
                video = getattr(shot, "video_generation", None)
                if video is not None and hasattr(video, "duration_seconds"):
                    video.duration_seconds = shot.duration_seconds
                total = sum(float(item.duration_seconds) for item in shots)
                changed = True
                if total >= minimum_seconds - 0.001:
                    break
        if not changed:
            break
    return total
