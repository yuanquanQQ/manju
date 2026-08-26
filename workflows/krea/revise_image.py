"""Revise one generated image with FLUX.1 Kontext while preserving lineage."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .generate_samples import ComfyClient, build_kontext_workflow
except ImportError:
    from generate_samples import ComfyClient, build_kontext_workflow


PRESERVATION_INSTRUCTIONS = {
    "strict": (
        "Make only the requested local correction. Keep every unrelated pixel-level "
        "choice as close as possible to the reference image."
    ),
    "balanced": (
        "Preserve identity, composition and art direction exactly, while allowing the "
        "small natural changes required to make the correction convincing."
    ),
    "creative": (
        "Preserve the same subject identity and story intent, but allow a broader "
        "re-render of pose, framing or styling when required by the request."
    ),
}


def build_revision_prompt(
    prompt: str,
    issue: str,
    negative_prompt: str,
    preservation: str,
) -> str:
    preservation_text = PRESERVATION_INSTRUCTIONS.get(
        preservation,
        PRESERVATION_INSTRUCTIONS["balanced"],
    )
    parts = [
        "Edit the provided image instead of creating an unrelated new image.",
        preservation_text,
        f"Problem to fix: {issue.strip()}." if issue.strip() else "",
        f"Requested final result: {prompt.strip()}." if prompt.strip() else "",
        (
            "Explicitly avoid in the revised image: "
            f"{negative_prompt.strip()}."
            if negative_prompt.strip()
            else ""
        ),
        (
            "Keep the same face identity, apparent age, gender, hairstyle, costume "
            "colors, body proportions, important props, lighting direction and "
            "background geometry unless the request explicitly changes one of them."
        ),
        "No text, caption, logo, watermark, duplicate person or unexplained new object.",
    ]
    return " ".join(part for part in parts if part)[:2600]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--issue", default="")
    parser.add_argument("--negative-prompt", default="")
    parser.add_argument(
        "--preservation",
        choices=tuple(PRESERVATION_INSTRUCTIONS),
        default="balanced",
    )
    parser.add_argument("--candidate-count", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--context-type", choices=("character", "shot"), required=True)
    parser.add_argument("--context-id", required=True)
    args = parser.parse_args()

    candidate_count = max(1, min(args.candidate_count, 4))
    prompt = build_revision_prompt(
        args.prompt,
        args.issue,
        args.negative_prompt,
        args.preservation,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = ComfyClient(args.comfy_url)
    client.health()
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "image_revisions",
        "editor": "flux_kontext",
        "models": [
            {
                "id": "flux_kontext",
                "label": "FLUX.1 Kontext Dev FP8",
                "file": "flux1-dev-kontext_fp8_scaled.safetensors",
                "architecture": "flux_kontext",
            }
        ],
        "source_image": args.source_image,
        "context_type": args.context_type,
        "context_id": args.context_id,
        "issue": args.issue,
        "requested_prompt": args.prompt,
        "negative_prompt": args.negative_prompt,
        "preservation": args.preservation,
        "width": args.width,
        "height": args.height,
        "generated_at": generated_at,
        "images": [],
    }

    for index in range(1, candidate_count + 1):
        seed = args.seed + index * 97
        stem = f"revision_flux_kontext_candidate_{index:02d}"
        destination = args.output_dir / f"{stem}.png"
        workflow = build_kontext_workflow(
            prompt,
            seed=seed,
            width=args.width,
            height=args.height,
            filename_prefix=f"revisions/{stem}",
            reference_image=args.source_image,
        )
        image = client.wait(client.queue(workflow))
        client.download(image, destination)
        record: dict[str, Any] = {
            "candidate": index,
            "seed": seed,
            "file": destination.name,
            "model_id": "flux_kontext",
            "model_label": "FLUX.1 Kontext Dev FP8",
            "model_file": "flux1-dev-kontext_fp8_scaled.safetensors",
            "prompt": prompt,
            "source_image": args.source_image,
            "issue": args.issue,
            "negative_prompt": args.negative_prompt,
            "preservation": args.preservation,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        if args.context_type == "character":
            record["character"] = args.context_id
            record["layout_label"] = "Kontext 修改版本"
        else:
            record["shot_number"] = int(args.context_id)
            record["frame_role"] = "start"
        manifest["images"].append(record)
        print(f"[PROGRESS] {index}/{candidate_count} revision", flush=True)

    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
