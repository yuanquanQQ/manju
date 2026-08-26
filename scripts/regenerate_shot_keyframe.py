"""Regenerate one storyboard keyframe on the configured GPU server."""

from __future__ import annotations

import argparse
from datetime import datetime

from app.services.desktop_service import DesktopProjectService
from app.services.gpu_service import GpuServerService, default_gpu_connection
from app.services.prompt_styles import DEFAULT_STYLE, style_prompt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--shot", type=int, required=True)
    parser.add_argument("--model", default="flux_krea")
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--candidates", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    projects = DesktopProjectService()
    episode = next(
        item
        for item in projects.load_episodes(args.project)
        if item.number == args.episode
    )
    output_dir = (
        projects.projects_dir
        / args.project
        / "production"
        / "shots"
        / f"episode_{args.episode:03d}"
        / "generated_video_safe"
        / datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    def progress(percent: int, message: str) -> None:
        print(f"PROGRESS={percent} {message}", flush=True)

    result = GpuServerService().generate_shot_images(
        default_gpu_connection(),
        project_slug=args.project,
        episode_path=episode.path,
        shot_numbers=[args.shot],
        model_ids=[args.model],
        local_output_dir=output_dir,
        candidate_count=max(1, min(args.candidates, 4)),
        seed=args.seed,
        width=832,
        height=480,
        style_prompt=style_prompt(DEFAULT_STYLE),
        progress_callback=progress,
    )
    manifest_path = output_dir / "manifest.json"
    saved: list[str] = []
    matching_records = [
        record
        for record in result.manifest.get("images") or []
        if int(record.get("shot_number") or 0) == args.shot
    ]
    for index, record in enumerate(matching_records):
        image_path = output_dir / str(record.get("file") or "")
        if not image_path.is_file():
            continue
        projects.save_shot_image_result(
            args.project,
            args.episode,
            args.shot,
            image_path,
            manifest_path,
            record,
            select=index == 0,
        )
        saved.append(str(image_path))
    if not saved:
        raise RuntimeError("The GPU returned no selectable keyframe.")
    print(f"SAVED={saved!r}", flush=True)
    print(f"ELAPSED={result.elapsed_seconds:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
