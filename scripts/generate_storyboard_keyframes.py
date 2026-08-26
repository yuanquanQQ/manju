"""Generate and register storyboard keyframes for one episode."""

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
    parser.add_argument("--shot", type=int, action="append", dest="shots")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--candidates", type=int, default=1)
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--style", default=DEFAULT_STYLE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    projects = DesktopProjectService()
    episode = next(
        item
        for item in projects.load_episodes(args.project)
        if item.number == args.episode
    )
    shot_numbers = args.shots or [shot.number for shot in episode.shots]
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
        shot_numbers=shot_numbers,
        model_ids=args.models or ["flux_krea"],
        local_output_dir=output_dir,
        candidate_count=max(1, min(args.candidates, 4)),
        seed=args.seed,
        width=args.width,
        height=args.height,
        style_prompt=style_prompt(args.style),
        progress_callback=progress,
    )
    manifest_path = output_dir / "manifest.json"
    selected: set[int] = set()
    saved: list[str] = []
    for record in result.manifest.get("images") or []:
        if not isinstance(record, dict):
            continue
        shot_number = int(record.get("shot_number") or 0)
        image_path = output_dir / str(record.get("file") or "")
        if not shot_number or not image_path.is_file():
            continue
        select = shot_number not in selected
        projects.save_shot_image_result(
            args.project,
            args.episode,
            shot_number,
            image_path,
            manifest_path,
            record,
            select=select,
        )
        if select:
            selected.add(shot_number)
        saved.append(str(image_path))

    missing = sorted(set(shot_numbers) - selected)
    if missing:
        raise RuntimeError(f"GPU 未返回这些镜头的可选首帧：{missing}")
    print(f"OUTPUT_DIR={output_dir}", flush=True)
    print(f"REGISTERED={len(selected)}", flush=True)
    print(f"CANDIDATES={len(saved)}", flush=True)
    print(f"ELAPSED={result.elapsed_seconds:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
