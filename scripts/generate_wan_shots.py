"""Generate selected storyboard shots with Wan2.2 and save them to the project."""

from __future__ import annotations

import argparse

from app.domain.video import VideoRenderSpec
from app.services.desktop_service import (
    DEFAULT_VIDEO_NEGATIVE_PROMPT,
    DesktopProjectService,
)
from app.services.gpu_service import GpuServerService, default_gpu_connection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--shot", type=int, action="append", dest="shots")
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=16)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    projects = DesktopProjectService()
    episode = next(
        item
        for item in projects.load_episodes(args.project)
        if item.number == args.episode
    )
    wanted = set(args.shots or [item.number for item in episode.shots])
    specs: list[VideoRenderSpec] = []
    for shot in episode.shots:
        if shot.number not in wanted:
            continue
        if shot.source_image is None:
            raise RuntimeError(f"Shot {shot.number} has no source image.")
        specs.append(
            VideoRenderSpec(
                episode_number=episode.number,
                shot_number=shot.number,
                source_image=shot.source_image,
                scene_description=shot.description,
                subject_motion=shot.subject_motion,
                environment_motion=shot.environment_motion,
                continuity_constraints=shot.continuity_constraints,
                negative_prompt=shot.negative_prompt
                or DEFAULT_VIDEO_NEGATIVE_PROMPT,
                motion_prompt=shot.motion_prompt,
                camera_movement=shot.camera_movement,
                motion_strength=shot.motion_strength,
                screen_direction=shot.screen_direction,
                transition_out=shot.transition_out,
                transition_frames=shot.transition_frames,
                handle_frames=shot.handle_frames,
                candidate_count=shot.candidate_count,
                duration_seconds=shot.duration_seconds,
                fps=args.fps,
                width=args.width,
                height=args.height,
                engine_profile="wan22_ti2v_5b",
            )
        )
    if not specs:
        raise RuntimeError("No matching shots were found.")

    def progress(percent: int, message: str) -> None:
        print(f"PROGRESS={percent} {message}", flush=True)

    result = GpuServerService().generate_wan_videos(
        default_gpu_connection(),
        projects.projects_dir / args.project,
        specs,
        progress_callback=progress,
    )
    for clip in result.clips:
        projects.save_shot_video_result(
            args.project,
            clip.episode_number,
            clip.shot_number,
            clip.video_path,
            clip.manifest_path,
            select=True,
        )
        print(f"VIDEO={clip.video_path}", flush=True)
    print(f"JOB={result.job_id}", flush=True)
    print(f"ELAPSED={result.elapsed_seconds:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
