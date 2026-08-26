"""Plan shot durations from spoken text before expensive video generation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)?|\d+(?:\.\d+)?")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[。！？!?；;])")
_CLAUSE_BREAK_RE = re.compile(r"(?<=[，,、：:])")
_SPEAKER_PREFIX_RE = re.compile(r"^[^：:\n]{1,16}[：:]\s*")


@dataclass(frozen=True, slots=True)
class AudioTimingSummary:
    shot_count: int
    changed_shots: int
    needs_split_shots: int
    total_duration_seconds: float
    estimated_speech_seconds: float


def estimate_speech_duration(text: str, rate: str = "+5%") -> float:
    """Estimate Mandarin speech duration without requiring a TTS service."""

    cleaned = _SPEAKER_PREFIX_RE.sub("", text.strip())
    if not cleaned:
        return 0.0
    han_count = len(_HAN_RE.findall(cleaned))
    word_count = len(_WORD_RE.findall(cleaned))
    other_count = sum(
        1
        for char in cleaned
        if not char.isspace()
        and not _HAN_RE.match(char)
        and char not in "，。！？!?；;、：:,.\"'“”‘’（）()—-"
    )
    base_seconds = han_count / 4.2 + word_count / 2.55 + other_count / 5.0
    pause_seconds = (
        sum(cleaned.count(mark) for mark in "。！？!?；;") * 0.28
        + sum(cleaned.count(mark) for mark in "，,、：:") * 0.12
    )
    match = re.fullmatch(r"([+-]?\d+)%", rate.strip())
    speed = 1.0 + ((int(match.group(1)) if match else 0) / 100)
    speed = max(0.55, min(speed, 1.8))
    return round(max(0.8, (base_seconds + pause_seconds) / speed), 3)


def split_spoken_text(text: str, *, max_chars: int = 24) -> list[str]:
    """Split a long line at natural punctuation for shot/reaction planning."""

    cleaned = _SPEAKER_PREFIX_RE.sub("", text.strip())
    if not cleaned:
        return []
    sentences = [
        part.strip()
        for part in _SENTENCE_BREAK_RE.split(cleaned)
        if part.strip()
    ]
    result: list[str] = []
    for sentence in sentences:
        clauses = [
            part.strip()
            for part in _CLAUSE_BREAK_RE.split(sentence)
            if part.strip()
        ]
        buffer = ""
        for clause in clauses:
            if not buffer:
                buffer = clause
                continue
            if len(buffer) + len(clause) <= max_chars:
                buffer += clause
            else:
                result.append(buffer)
                buffer = clause
        if buffer:
            while len(buffer) > max_chars:
                result.append(buffer[:max_chars])
                buffer = buffer[max_chars:]
            if buffer:
                result.append(buffer)
    return result or [cleaned]


def optimize_episode_audio_timing(
    episode: dict[str, Any],
    *,
    minimum_episode_seconds: float = 60.0,
    lead_seconds: float = 0.15,
    tail_seconds: float = 0.25,
    preferred_max_shot_seconds: float = 6.0,
    hard_max_shot_seconds: float = 15.0,
) -> AudioTimingSummary:
    """Write estimated speech/timeline durations into an episode payload."""

    shots = [
        shot
        for shot in (episode.get("shots") or [])
        if isinstance(shot, dict)
    ]
    changed = 0
    needs_split = 0
    estimated_total = 0.0
    for shot in shots:
        audio = shot.get("audio_generation")
        audio = dict(audio) if isinstance(audio, dict) else {}
        video = shot.get("video_generation")
        video = dict(video) if isinstance(video, dict) else {}
        mode = str(audio.get("mode") or "auto_narration")
        speaker = str(audio.get("speaker") or "旁白").strip() or "旁白"
        text = str(
            audio.get("text")
            or shot.get("dialogue")
            or (
                shot.get("scene_description")
                if mode == "auto_narration"
                else ""
            )
            or ""
        ).strip()
        current = max(
            1.0,
            float(
                video.get("duration_seconds")
                or shot.get("duration_seconds")
                or 3.0
            ),
        )
        before = current
        if mode == "mute" or not text:
            speech_seconds = 0.0
            planned = current
            segments: list[str] = []
            status = "mute" if mode == "mute" else "no_text"
            recommended_segments = 1
        else:
            speech_seconds = estimate_speech_duration(
                text,
                str(audio.get("rate") or "+5%"),
            )
            estimated_total += speech_seconds
            planned = round(
                max(current, speech_seconds + lead_seconds + tail_seconds),
                2,
            )
            segments = split_spoken_text(text)
            recommended_segments = max(
                1,
                len(segments),
                math.ceil(planned / preferred_max_shot_seconds),
            )
            if recommended_segments > 1:
                status = "needs_split"
                needs_split += 1
            elif planned > current + 0.1:
                status = "needs_regeneration"
            else:
                status = "ready"
        target = min(planned, hard_max_shot_seconds)
        shot["duration_seconds"] = round(target, 2)
        video["duration_seconds"] = round(target, 2)
        audio.update(
            {
                "estimated_duration_seconds": round(speech_seconds, 3),
                "planned_timeline_duration_seconds": round(planned, 3),
                "timing_status": status,
                "recommended_segments": recommended_segments,
                "segments": [
                    {
                        "index": index,
                        "text": segment,
                        "estimated_duration_seconds": estimate_speech_duration(
                            segment,
                            str(audio.get("rate") or "+5%"),
                        ),
                    }
                    for index, segment in enumerate(segments, start=1)
                ],
            }
        )
        shot["audio_generation"] = audio
        shot["video_generation"] = video
        existing_lip_sync = shot.get("lip_sync")
        lip_sync = (
            dict(existing_lip_sync)
            if isinstance(existing_lip_sync, dict)
            else {}
        )
        if not isinstance(existing_lip_sync, dict):
            enabled = mode == "dialogue" and speaker != "旁白" and bool(text)
            lip_sync.update(
                {
                    "enabled": enabled,
                    "engine": "latentsync_1_6",
                    "target_character": speaker if enabled else "",
                    "mode": "speaker_tracking",
                    "status": "pending" if enabled else "disabled",
                }
            )
        elif bool(lip_sync.get("enabled")):
            lip_sync.setdefault("target_character", speaker)
            lip_sync.setdefault("status", "pending")
        shot["lip_sync"] = lip_sync
        if abs(target - before) > 0.01 or status not in {"ready", "mute"}:
            changed += 1

    total = sum(
        float(
            (shot.get("video_generation") or {}).get("duration_seconds")
            or shot.get("duration_seconds")
            or 3.0
        )
        for shot in shots
    )
    remaining = max(0.0, minimum_episode_seconds - total)
    eligible = list(shots)
    while remaining > 0.01 and eligible:
        share = remaining / len(eligible)
        next_round: list[dict[str, Any]] = []
        distributed = 0.0
        for shot in eligible:
            video = shot["video_generation"]
            current = float(video["duration_seconds"])
            available = preferred_max_shot_seconds - current
            addition = max(0.0, min(share, available))
            if addition > 0:
                target = round(current + addition, 2)
                video["duration_seconds"] = target
                shot["duration_seconds"] = target
                distributed += target - current
            if available - addition > 0.01:
                next_round.append(shot)
        if distributed <= 0.01:
            break
        remaining -= distributed
        eligible = next_round
    total = sum(
        float((shot.get("video_generation") or {}).get("duration_seconds") or 0)
        for shot in shots
    )
    while total < minimum_episode_seconds - 0.001:
        adjusted = False
        for shot in shots:
            video = shot["video_generation"]
            current = float(video["duration_seconds"])
            if current + 0.01 > preferred_max_shot_seconds:
                continue
            target = round(current + 0.01, 2)
            video["duration_seconds"] = target
            shot["duration_seconds"] = target
            total = sum(
                float(
                    (item.get("video_generation") or {}).get(
                        "duration_seconds"
                    )
                    or 0
                )
                for item in shots
            )
            adjusted = True
            if total >= minimum_episode_seconds - 0.001:
                break
        if not adjusted:
            break
    return AudioTimingSummary(
        shot_count=len(shots),
        changed_shots=changed,
        needs_split_shots=needs_split,
        total_duration_seconds=round(total, 2),
        estimated_speech_seconds=round(estimated_total, 2),
    )
