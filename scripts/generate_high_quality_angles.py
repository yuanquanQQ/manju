"""Generate identity-locked character angles from one approved casting portrait."""

from __future__ import annotations

import argparse
import json
import re
import shlex
from datetime import datetime
from pathlib import Path

from app.services.gpu_service import GpuServerService
from scripts.generate_high_quality_cast import (
    _connection,
    _download_tree,
    _preflight,
)


def run(args: argparse.Namespace) -> Path:
    workspace = args.workspace.resolve()
    source = args.source_image.resolve()
    approval = args.approval_file.resolve()
    if not source.is_file() or not approval.is_file():
        raise FileNotFoundError("approved cast source or approval file is missing")
    approval_value = json.loads(approval.read_text(encoding="utf-8"))
    if approval_value.get("status") != "approved":
        raise RuntimeError("cast source has not been approved")

    service = GpuServerService()
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_character = re.sub(r"[^A-Za-z0-9_-]+", "_", args.character).strip("_") or "cast"
    remote_workflow_dir = f"{service.remote_project_root}/workflows/chinese_cast"
    remote_workflow = f"{remote_workflow_dir}/generate_cast.py"
    remote_dir = (
        f"{service.remote_project_root}/outputs/chinese_cast_angles/{run_id}_{safe_character}"
    )
    remote_source = f"{remote_dir}/approved_source{source.suffix.lower()}"
    remote_approval = f"{remote_dir}/approval.json"
    local_dir = (
        workspace
        / "projects"
        / args.project
        / "outputs"
        / "chinese_cast_angles"
        / f"{run_id}_{args.character}"
    )
    workflow = workspace / "workflows" / "chinese_cast" / "generate_cast.py"

    client = service._connect(_connection(workspace))
    try:
        service._ensure_remote_comfy(client)
        _preflight(service, client, stage="all")
        service._exec(
            client,
            f"mkdir -p {shlex.quote(remote_workflow_dir)} {shlex.quote(remote_dir)}",
            timeout=15,
        )
        remote_approval_value = {
            **approval_value,
            "source_image": remote_source,
            "local_approval_file": str(approval),
        }
        sftp = client.open_sftp()
        try:
            sftp.put(str(workflow), remote_workflow)
            sftp.put(str(source), remote_source)
            with sftp.file(remote_approval, "wb") as handle:
                handle.write(
                    json.dumps(remote_approval_value, ensure_ascii=False, indent=2).encode("utf-8")
                )
        finally:
            sftp.close()
        command = " ".join(
            [
                "cd",
                shlex.quote(service.remote_project_root),
                "&&",
                "/root/miniconda3/bin/python",
                shlex.quote(remote_workflow),
                "angles",
                "--source-image",
                shlex.quote(remote_source),
                "--approval-file",
                shlex.quote(remote_approval),
                "--output-dir",
                shlex.quote(remote_dir),
                "--run-name",
                shlex.quote(f"{run_id}_{args.character}"),
                "--candidate-count",
                str(args.count),
                "--seed",
                str(args.seed),
                "--lora-strength",
                str(args.lora_strength),
            ]
        )
        service._exec_streaming(
            client,
            command,
            timeout=21600,
            output_callback=lambda line: print(line, flush=True),
        )
        _download_tree(service, client, remote_dir, local_dir)
    finally:
        client.close()
    (local_dir / "source_approval.json").write_text(
        json.dumps(approval_value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OUTPUT_ROOT] {local_dir}", flush=True)
    return local_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="jueshi")
    parser.add_argument("--character", required=True)
    parser.add_argument("--source-image", type=Path, required=True)
    parser.add_argument("--approval-file", type=Path, required=True)
    parser.add_argument("--count", type=int, default=2, choices=range(2, 5))
    parser.add_argument("--seed", type=int, default=2026082601)
    parser.add_argument("--lora-strength", type=float, default=1.0)
    parser.add_argument(
        "--run-id",
        help="reuse a previous remote run directory and resume completed candidates",
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
