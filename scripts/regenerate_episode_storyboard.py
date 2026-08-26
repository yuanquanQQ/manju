"""Regenerate one episode storyboard from its imported source chapter."""

from __future__ import annotations

import argparse

from app.core.config import settings
from app.database.db import init_db
from app.pipeline.storyboard import generate_storyboard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--episode", type=int, required=True)
    args = parser.parse_args()

    root = (settings.projects_dir / args.project).resolve()
    init_db(root / "database" / "world.db")

    def progress(done: int, total: int, message: str) -> None:
        print(f"PROGRESS={done}/{total} {message}", flush=True)

    episodes = generate_storyboard(
        root,
        start=args.episode,
        end=args.episode,
        progress_callback=progress,
    )
    if not episodes:
        raise RuntimeError("没有生成任何分镜")
    episode = episodes[0]
    duration = sum(shot.duration_seconds for shot in episode.shots)
    print(f"SHOTS={len(episode.shots)}", flush=True)
    print(f"DURATION={duration:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
