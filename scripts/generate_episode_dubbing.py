"""Generate speech, subtitles, and a dubbed episode from saved shot settings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.domain.audio import DubbingLineSpec
from app.services.audio_service import DubbingService
from app.services.desktop_service import DesktopProjectService


def _resume_audio(
    audio_dir: Path,
    run_id: str,
    *,
    shot_number: int,
    text: str,
    voice_id: str,
    engine: str,
    rate: str,
    pitch: str,
) -> Path | None:
    manifest_path = (
        audio_dir / f"manifest_shot_{shot_number:03d}_{run_id}.json"
    )
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    expected = {
        "text": text,
        "voice_id": voice_id,
        "engine": engine,
        "rate": rate,
        "pitch": pitch,
    }
    if any(str(manifest.get(key) or "") != value for key, value in expected.items()):
        return None
    for suffix in (".wav", ".mp3"):
        candidate = audio_dir / f"shot_{shot_number:03d}_{run_id}{suffix}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _existing_audio(
    project_root: Path,
    manifest_path: Path | None,
    *,
    shot_number: int,
    text: str,
    speaker: str,
    voice_id: str,
    engine: str,
    rate: str,
    pitch: str,
) -> Path | None:
    if manifest_path is None or not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    line = next(
        (
            item
            for item in manifest.get("lines") or []
            if isinstance(item, dict)
            and int(item.get("shot_number") or 0) == shot_number
        ),
        None,
    )
    if line is None:
        return None
    expected = {
        "text": text,
        "speaker": speaker,
        "voice_id": voice_id,
        "engine": engine,
    }
    if any(str(line.get(key) or "") != value for key, value in expected.items()):
        return None
    for key, expected_value in (("rate", rate), ("pitch", pitch)):
        recorded = str(line.get(key) or "")
        if recorded and recorded != expected_value:
            return None
    configured = str(line.get("audio_file") or "")
    if not configured:
        return None
    candidate = (project_root / configured).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError:
        return None
    return (
        candidate
        if candidate.is_file() and candidate.stat().st_size > 0
        else None
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument(
        "--resume-run",
        default="",
        help="Reuse completed shot audio from an interrupted dubbing run id.",
    )
    parser.add_argument(
        "--reuse-existing-audio",
        action="store_true",
        help="Reuse matching audio from the episode's latest dubbing manifest.",
    )
    parser.add_argument(
        "--ai-label",
        action="store_true",
        help="Burn a visible AI-generated-content label into the output video.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    projects = DesktopProjectService()
    episode = next(
        item
        for item in projects.load_episodes(args.project)
        if item.number == args.episode
    )
    specs: list[DubbingLineSpec] = []
    resume_audio_dir = (
        projects.projects_dir
        / args.project
        / "production"
        / "audio"
        / f"episode_{episode.number:03d}"
    )
    project_root = projects.projects_dir / args.project
    for shot in episode.shots:
        text = shot.dialogue.strip()
        if shot.audio_mode == "auto_narration" and not text:
            text = shot.description.strip()
        if (
            (not text and shot.audio_mode != "mute")
            or shot.video_path is None
            or not shot.video_path.is_file()
        ):
            continue
        prepared_audio = (
            shot.audio_path
            if shot.lip_sync_status == "succeeded"
            and shot.audio_path is not None
            and shot.audio_path.is_file()
            else None
        )
        if prepared_audio is None and args.resume_run:
            prepared_audio = _resume_audio(
                resume_audio_dir,
                args.resume_run,
                shot_number=shot.number,
                text=text,
                voice_id=shot.voice_id,
                engine=shot.tts_engine,
                rate=shot.speech_rate,
                pitch=shot.speech_pitch,
            )
        if prepared_audio is None and args.reuse_existing_audio:
            prepared_audio = _existing_audio(
                project_root,
                episode.dubbing_manifest_path,
                shot_number=shot.number,
                text=text,
                speaker=shot.speaker,
                voice_id=shot.voice_id,
                engine=shot.tts_engine,
                rate=shot.speech_rate,
                pitch=shot.speech_pitch,
            )
        specs.append(
            DubbingLineSpec(
                episode_number=episode.number,
                shot_number=shot.number,
                source_video=shot.video_path,
                prepared_audio=prepared_audio,
                mode=shot.audio_mode,
                text=text,
                speaker=shot.speaker,
                voice_id=shot.voice_id,
                engine=shot.tts_engine,
                reference_audio=shot.voice_reference_path,
                reference_text=shot.voice_reference_text,
                instruct_text=shot.voice_instruct_text,
                fallback_to_edge=shot.fallback_to_edge,
                rate=shot.speech_rate,
                volume=shot.speech_volume,
                pitch=shot.speech_pitch,
                subtitle_enabled=shot.subtitle_enabled,
                lead_seconds=0.0 if prepared_audio is not None else 0.15,
            )
        )

    def progress(percent: int, message: str) -> None:
        print(f"PROGRESS={percent} {message}", flush=True)

    result = DubbingService().dub_episode(
        projects.projects_dir / args.project,
        episode.number,
        specs,
        progress_callback=progress,
        visible_ai_label=args.ai_label,
    )
    for line in result.lines:
        projects.save_shot_audio_result(
            args.project,
            line.episode_number,
            line.shot_number,
            line.audio_path,
            line.subtitle_path,
            line.manifest_path,
        )
    projects.save_episode_dubbing_result(
        args.project,
        result.episode_number,
        result.video_path,
        result.subtitle_path,
        result.manifest_path,
    )
    print(f"VIDEO={result.video_path}", flush=True)
    print(f"SUBTITLES={result.subtitle_path}", flush=True)
    print(f"MANIFEST={result.manifest_path}", flush=True)
    print(f"ELAPSED={result.elapsed_seconds:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
