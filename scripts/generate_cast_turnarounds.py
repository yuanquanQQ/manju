"""Generate strict white-background three-view sheets for an episode cast."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

from app.services.gpu_service import GpuConnection, GpuServerService
from app.services.prompt_styles import style_prompt
from scripts.generate_episode_h3 import _ssh_password


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="jueshi")
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument("--character", action="append", dest="characters")
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2026082601)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    project_root = workspace / "projects" / args.project
    episode_path = (
        project_root
        / "production"
        / "episodes"
        / f"episode_{args.episode:03d}.json"
    )
    episode = json.loads(episode_path.read_text(encoding="utf-8-sig"))
    profiles = episode.get("character_profiles") or {}
    requested = args.characters or list(profiles)
    missing = [name for name in requested if name not in profiles]
    if missing:
        raise KeyError(f"分镜中不存在角色：{missing}")

    env = dotenv_values(workspace / ".env")
    config = GpuConnection(
        host=str(env.get("GPU_SSH_HOST") or ""),
        port=int(env.get("GPU_SSH_PORT") or 22),
        username=str(env.get("GPU_SSH_USER") or "root"),
        password=_ssh_password(workspace / "ssh.txt"),
    )
    service = GpuServerService()
    run_root = (
        project_root
        / "outputs"
        / "cast_turnarounds"
        / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    for index, character in enumerate(requested, start=1):
        output_dir = run_root / f"{index:02d}_{character}"

        def progress(percent: int, message: str, *, name: str = character) -> None:
            print(f"[PROGRESS] character={name} percent={percent} {message}", flush=True)

        print(f"[START] character={character}", flush=True)
        result = service.generate_character(
            config,
            project_slug=args.project,
            episode_path=episode_path,
            character=character,
            model_ids=["flux_krea"],
            layout_preset="turnaround_no_bg",
            count=max(1, min(args.count, 8)),
            seed=args.seed + (index - 1) * 100,
            local_output_dir=output_dir,
            prompt=str(profiles[character]),
            style_prompt=style_prompt("真人电影"),
            progress_callback=progress,
        )
        print(
            f"[DONE] character={character} images={len(result.images)} "
            f"elapsed={result.elapsed_seconds:.1f} dir={result.local_dir}",
            flush=True,
        )
    print(f"[OUTPUT_ROOT] {run_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
