"""Generate resumable per-shot LatentSync results for one episode.

This is the command-line counterpart of the desktop application's
"batch episode lip-sync" action.  Completed shots are skipped by default and
one failed shot does not prevent the remaining shots from running.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from app.domain.audio import DubbingLineSpec
from app.services.audio_service import DubbingService
from app.services.desktop_service import DesktopProjectService
from app.services.gpu_service import GpuServerService, default_gpu_connection
from app.services.latentsync_service import LatentSyncRemoteService
from app.services.lip_sync_batch_service import LipSyncBatchPlanner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--shot", type=int, action="append", dest="shots")
    parser.add_argument("--minimum-face-similarity", type=float, default=0.18)
    parser.add_argument("--regenerate-completed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    projects = DesktopProjectService()
    project_root = (projects.projects_dir / args.project).resolve()
    planner = LipSyncBatchPlanner()
    plan = planner.plan(
        project_root,
        args.episode,
        regenerate_completed=args.regenerate_completed,
    )
    wanted = set(args.shots or [item.shot_number for item in plan.ready])
    items = [item for item in plan.ready if item.shot_number in wanted]
    if not items:
        print("PROGRESS=100 No ready lip-sync shots matched the request.", flush=True)
        return 0

    gpu = GpuServerService()
    config = default_gpu_connection()
    latentsync = LatentSyncRemoteService(gpu)
    dubbing = DubbingService()
    comfy_was_online = gpu.check_status(config).comfy_online
    completed: list[int] = []
    failed: dict[int, str] = {}
    total = len(items)
    try:
        for index, item in enumerate(items, start=1):
            assert item.source_video is not None
            start_percent = int((index - 1) * 100 / total)
            span = max(1, int(100 / total))

            def report(
                percent: int,
                message: str,
                *,
                _start: int = start_percent,
                _span: int = span,
                _shot: int = item.shot_number,
                _index: int = index,
            ) -> None:
                overall = min(99, _start + int(_span * percent / 100))
                print(
                    f"PROGRESS={overall} Shot {_shot:02d} "
                    f"({_index}/{total}): {message}",
                    flush=True,
                )

            try:
                if item.tts_engine != "edge_tts":
                    raise RuntimeError(
                        "The command-line batch currently supports edge_tts; "
                        f"shot {item.shot_number:02d} requests {item.tts_engine}."
                    )
                spec = DubbingLineSpec(
                    episode_number=args.episode,
                    shot_number=item.shot_number,
                    source_video=item.source_video,
                    mode="dialogue",
                    text=item.text,
                    speaker=item.speaker,
                    voice_id=item.voice_id,
                    engine="edge_tts",
                    reference_audio=item.reference_audio,
                    reference_text=item.reference_text,
                    instruct_text=item.instruct_text,
                    fallback_to_edge=item.fallback_to_edge,
                    rate=item.rate,
                    volume=item.volume,
                    pitch=item.pitch,
                    subtitle_enabled=True,
                )
                audio_path = (
                    project_root
                    / "production"
                    / "audio"
                    / f"episode_{args.episode:03d}"
                    / (
                        f"shot_{item.shot_number:03d}_lipsync_batch_"
                        f"{datetime.now():%Y%m%d_%H%M%S}.mp3"
                    )
                )
                report(2, "synthesizing dialogue audio")
                generated_audio = dubbing.synthesize_preview(spec, audio_path)
                result = latentsync.synchronize(
                    config,
                    project_root,
                    episode_number=args.episode,
                    shot_number=item.shot_number,
                    source_video=item.source_video,
                    audio_path=generated_audio,
                    inference_steps=item.inference_steps,
                    guidance_scale=item.guidance_scale,
                    target_character=item.target_character,
                    face_reference=item.face_reference,
                    face_selection_mode=item.face_selection_mode,
                    minimum_face_similarity=args.minimum_face_similarity,
                    restore_comfy=False,
                    progress_callback=lambda percent, message: report(
                        15 + int(percent * 0.85),
                        message,
                    ),
                )
                projects.save_lip_sync_result(
                    args.project,
                    result.episode_number,
                    result.shot_number,
                    result.video_path,
                    result.audio_path,
                    result.source_video,
                    result.manifest_path,
                    elapsed_seconds=result.elapsed_seconds,
                    face_match_similarity=result.face_match_similarity,
                    select=True,
                )
                completed.append(item.shot_number)
                print(
                    f"VIDEO={result.video_path} "
                    f"FACE_SIMILARITY={result.face_match_similarity:.4f}",
                    flush=True,
                )
            except Exception as exc:  # Continue so the next run can resume failures.
                detail = str(exc)
                projects.save_lip_sync_failure(
                    args.project,
                    args.episode,
                    item.shot_number,
                    detail,
                )
                failed[item.shot_number] = detail
                print(f"FAILED={item.shot_number}: {detail}", flush=True)
    finally:
        if comfy_was_online:
            print("PROGRESS=99 Restoring ComfyUI", flush=True)
            try:
                gpu.start_comfy(config)
            except Exception as exc:
                failed[0] = f"ComfyUI restore failed: {exc}"
                print(f"FAILED=0: {failed[0]}", flush=True)

    print(f"COMPLETED={completed}", flush=True)
    print(f"FAILED_SHOTS={failed}", flush=True)
    print("PROGRESS=100 Lip-sync batch finished", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
