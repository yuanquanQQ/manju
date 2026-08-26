"""Generate reviewed keyframe candidates for one shot on the GPU server."""

from __future__ import annotations

import argparse
import json
import shlex
from datetime import datetime
from pathlib import Path

from app.services.gpu_service import GpuServerService
from scripts.generate_high_quality_cast import (
    _connection,
    _download_tree,
    _preflight,
)


def build_shot_prompt(shot: dict, frame_role: str) -> str:
    video = shot.get("video_generation") or {}
    continuity = shot.get("continuity_plan") or {}
    if frame_role == "end":
        requested = str(
            video.get("end_frame_prompt")
            or video.get("subject_motion")
            or video.get("motion_prompt")
            or shot.get("scene_description")
            or ""
        ).strip()
        return (
            f"{requested}。这是动作结束状态，不是新镜头；保持画面轴线和空间关系，"
            "只完成一个动作节拍。"
        )
    parts = [
        str(continuity.get("keyframe_prompt") or "").strip(),
        str(shot.get("image_prompt") or "").strip(),
        f"镜头叙事：{str(shot.get('scene_description') or '').strip()}",
        f"连续性：{str(video.get('continuity_constraints') or '').strip()}",
        "中国古装真人电影质感；这是动作发生前一瞬的可动画首帧，不是人物摆拍。",
    ]
    return "\n".join(part for part in parts if part)


def _find_shot(episode: dict, shot_number: int) -> dict:
    for index, shot in enumerate(episode.get("shots") or [], start=1):
        if int(shot.get("shot_number") or index) == shot_number:
            return shot
    raise KeyError(f"episode does not contain shot {shot_number}")


def _resolve_references(
    args: argparse.Namespace,
    project_root: Path,
    shot: dict,
) -> list[Path]:
    values = list(args.reference_image or [])
    if args.frame_role == "end" and not values:
        video = shot.get("video_generation") or {}
        image = shot.get("image_generation") or {}
        selected = str(
            video.get("source_image")
            or image.get("selected_image")
            or image.get("selected_source")
            or ""
        )
        if selected:
            values.append(selected)
    paths: list[Path] = []
    for value in values:
        candidate = Path(value)
        candidate = candidate if candidate.is_absolute() else project_root / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"reference image does not exist: {candidate}")
        paths.append(candidate)
    if not paths:
        raise RuntimeError(
            "start frame needs approved cast anchors; end frame needs the approved start frame"
        )
    return paths


def run(args: argparse.Namespace) -> Path:
    workspace = args.workspace.resolve()
    project_root = (workspace / "projects" / args.project).resolve()
    episode_path = (
        project_root
        / "production"
        / "episodes"
        / f"episode_{args.episode:03d}.json"
    )
    episode = json.loads(episode_path.read_text(encoding="utf-8-sig"))
    shot = _find_shot(episode, args.shot)
    references = _resolve_references(args, project_root, shot)
    prompt = build_shot_prompt(shot, args.frame_role)

    service = GpuServerService()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    remote_workflow_dir = f"{service.remote_project_root}/workflows/high_quality_image"
    remote_workflow = f"{remote_workflow_dir}/generate_keyframe.py"
    remote_shared_dir = f"{service.remote_project_root}/workflows/chinese_cast"
    remote_shared = f"{remote_shared_dir}/generate_cast.py"
    remote_dir = (
        f"{service.remote_project_root}/outputs/high_quality_keyframes/"
        f"{run_id}_shot_{args.shot:03d}_{args.frame_role}"
    )
    remote_prompt = f"{remote_dir}/prompt.txt"
    remote_refs = [
        f"{remote_dir}/reference_{index:02d}{path.suffix.lower()}"
        for index, path in enumerate(references, start=1)
    ]
    local_dir = (
        project_root
        / "outputs"
        / "high_quality_keyframes"
        / f"{run_id}_shot_{args.shot:03d}_{args.frame_role}"
    )
    workflow = workspace / "workflows" / "high_quality_image" / "generate_keyframe.py"
    shared_workflow = workspace / "workflows" / "chinese_cast" / "generate_cast.py"

    client = service._connect(_connection(workspace))
    try:
        service._ensure_remote_comfy(client)
        _preflight(service, client)
        service._exec(
            client,
            "mkdir -p "
            f"{shlex.quote(remote_workflow_dir)} {shlex.quote(remote_shared_dir)} "
            f"{shlex.quote(remote_dir)}",
            timeout=15,
        )
        sftp = client.open_sftp()
        try:
            sftp.put(str(workflow), remote_workflow)
            sftp.put(str(shared_workflow), remote_shared)
            for source, destination in zip(references, remote_refs, strict=True):
                sftp.put(str(source), destination)
            with sftp.file(remote_prompt, "wb") as handle:
                handle.write(prompt.encode("utf-8"))
        finally:
            sftp.close()
        command = [
            "/root/miniconda3/bin/python",
            shlex.quote(remote_workflow),
            "--prompt-file",
            shlex.quote(remote_prompt),
            "--output-dir",
            shlex.quote(remote_dir),
            "--run-name",
            shlex.quote(
                f"{run_id}_shot_{args.shot:03d}_{args.frame_role}"
            ),
            "--frame-role",
            args.frame_role,
            "--candidate-count",
            str(args.count),
            "--seed",
            str(args.seed),
            "--width",
            str(args.width),
            "--height",
            str(args.height),
        ]
        for remote_ref in remote_refs:
            command.extend(("--reference-image", shlex.quote(remote_ref)))
        service._exec_streaming(
            client,
            f"cd {shlex.quote(service.remote_project_root)} && "
            + " ".join(command),
            timeout=14400,
            output_callback=lambda line: print(line, flush=True),
        )
        _download_tree(service, client, remote_dir, local_dir)
    finally:
        client.close()
    (local_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    print(f"[OUTPUT_ROOT] {local_dir}", flush=True)
    return local_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="jueshi")
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument("--shot", type=int, required=True)
    parser.add_argument("--frame-role", choices=("start", "end"), default="start")
    parser.add_argument("--reference-image", action="append")
    parser.add_argument("--count", type=int, default=2, choices=range(2, 5))
    parser.add_argument("--seed", type=int, default=2026082601)
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
