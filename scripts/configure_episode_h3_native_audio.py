"""Configure one episode to use MiniMax H3 joint native audio-video output."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from app.core.files import atomic_write_json
from scripts.generate_episode_h3 import _native_dialogue_prompt


def run(project: str, episode_number: int, workspace: Path) -> Path:
    episode_path = (
        workspace.resolve()
        / "projects"
        / project
        / "production"
        / "episodes"
        / f"episode_{episode_number:03d}.json"
    )
    episode = json.loads(episode_path.read_text(encoding="utf-8-sig"))
    backup = episode_path.with_name(
        f"{episode_path.stem}.before_h3_native_audio_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    shutil.copy2(episode_path, backup)

    for shot in episode.get("shots") or []:
        video = shot.setdefault("video_generation", {})
        video["engine_profile"] = "minimax_h3_fl2va"
        video["native_audio_mode"] = "native_full"
        # Persist only the literal line plus speaker metadata.  The H3 prompt
        # builder adds engine instructions later; never store those as speech.
        video["dialogue_prompt"] = _native_dialogue_prompt(shot, compact=True)
        video["candidate_count"] = max(2, int(video.get("candidate_count") or 0))
        video["selected_video"] = ""
        video["manifest_file"] = ""
        video["candidates"] = []

        audio = shot.setdefault("audio_generation", {})
        audio["enabled"] = False
        audio["mode_before_h3_native_audio"] = audio.get("mode", "")
        audio["mode"] = "h3_native_full"
        audio["audio_file"] = ""
        audio["subtitle_file"] = ""
        audio["manifest_file"] = ""

        lip_sync = shot.setdefault("lip_sync", {})
        lip_sync["enabled"] = False
        lip_sync["status"] = "not_required_h3_native_audio"
        lip_sync["source_video"] = ""
        lip_sync["audio_file"] = ""
        lip_sync["output_file"] = ""
        lip_sync["manifest_file"] = ""

    episode["audio_workflow"] = {
        "mode": "h3_native_full",
        "separate_tts": False,
        "separate_lip_sync": False,
        "voice_continuity": "prompt_locked_per_character",
        "candidate_audio_review_required": True,
        "configured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "backup": backup.name,
    }
    atomic_write_json(episode_path, episode)
    print(f"[BACKUP] {backup}", flush=True)
    print(f"[UPDATED] {episode_path}", flush=True)
    return episode_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="jueshi")
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.project, args.episode, args.workspace)


if __name__ == "__main__":
    main()
