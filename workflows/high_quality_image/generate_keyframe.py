"""Two-stage, identity-locked keyframe generation for one reviewed shot."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from workflows.chinese_cast.generate_cast import (
    QWEN_EDIT_MODEL,
    Z_IMAGE_MODEL,
    _copy_comfy_input,
    build_qwen_edit_workflow,
    build_z_image_workflow,
    submit_workflow,
    technical_qc,
)


def build_identity_edit_prompt(prompt: str, identity_count: int) -> str:
    references = "、".join(f"图{i}" for i in range(2, identity_count + 2))
    return (
        "以图1的场景构图、机位、景别、光线和人物动作作为画面底稿。"
        f"{references or '后续参考图'}是已批准的演员身份和服装锚点；将底稿中对应角色"
        "精确替换为锚点中的同一位中国演员，严格保持其脸型、五官比例、年龄、发际线、"
        "发型、服装剪裁、层次、纹样、材质和颜色。不得把不同人物的五官或服装互相融合。"
        "保留图1的透视、人物位置、遮挡关系、视线、道具和背景几何，不要缩放、裁切、"
        "重构场景或增加人物。面部必须清晰自然，双眼一致，手指和肢体解剖正确，真人电影"
        "质感，不要网红妆、塑料皮肤、伪文字或水印。\n\n"
        f"最终镜头要求：{prompt}"
    )


def build_end_frame_prompt(prompt: str) -> str:
    return (
        "图1是已经批准的同一镜头首帧，只编辑人物动作到一个物理上可达的结束状态。"
        "严格保持全部演员身份、五官、年龄、发型、服装、道具、建筑、背景几何、光线、"
        "焦距、机位、构图和画面方向不变。不得重新造景，不得换脸换装，不得新增、删除或"
        "移动无关人物。动作幅度只覆盖一个连续镜头内能够自然完成的单一动作，保留干净"
        "稳定的结尾姿势供视频模型对齐；双手、双脚、接触关系和重心必须合理。\n\n"
        f"结束状态：{prompt}"
    )


def _manifest(
    *,
    args: argparse.Namespace,
    prompt: str,
    outputs: list[dict],
    elapsed_seconds: float,
) -> dict:
    return {
        "schema_version": "2.0",
        "stage": "identity_locked_keyframe",
        "frame_role": args.frame_role,
        "base_model": Z_IMAGE_MODEL if args.frame_role == "start" else None,
        "identity_edit_model": QWEN_EDIT_MODEL,
        "quantized": True,
        "prompt": prompt,
        "references": [str(Path(item).resolve()) for item in args.reference_image],
        "outputs": outputs,
        "gate": {
            "status": "blocked_until_human_identity_composition_review",
            "review_checks": [
                "correct Chinese actor identity for every visible principal",
                "face and costume match approved turnaround",
                "story action, eyelines, props and screen direction are correct",
                "hands, anatomy, text and background are artifact-free",
                "next shot boundary is compatible with transition plan",
            ],
        },
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def run(args: argparse.Namespace) -> dict:
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("keyframe prompt is empty")
    references = [Path(item).resolve() for item in args.reference_image]
    if not references or any(not item.is_file() for item in references):
        raise FileNotFoundError("all keyframes require approved local reference images")
    if args.frame_role == "end" and len(references) < 1:
        raise ValueError("end frame requires the approved start frame")
    if len(references) > (3 if args.frame_role == "end" else 2):
        raise ValueError("start frames support two cast anchors; end frames support three refs")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    comfy_root = Path(args.comfy_root).resolve()
    reference_names = [_copy_comfy_input(comfy_root, item) for item in references]
    started = time.monotonic()
    outputs: list[dict] = []
    for index in range(1, args.candidate_count + 1):
        seed = args.seed + index * 9973
        base_path: Path | None = None
        if args.frame_role == "start":
            base_path = output_dir / f"candidate_{index:02d}_composition.png"
            base_id = submit_workflow(
                args.comfy_url,
                build_z_image_workflow(
                    prompt,
                    seed=seed,
                    width=args.width,
                    height=args.height,
                    filename_prefix=(
                        f"novel2anime/keyframes/{args.run_name}/"
                        f"candidate_{index:02d}_composition"
                    ),
                    steps=args.base_steps,
                ),
                base_path,
                timeout_seconds=args.timeout_seconds,
            )
            base_name = _copy_comfy_input(comfy_root, base_path)
            edit_names = [base_name, *reference_names]
            edit_prompt = build_identity_edit_prompt(prompt, len(references))
        else:
            base_id = ""
            edit_names = reference_names
            edit_prompt = build_end_frame_prompt(prompt)

        final_path = output_dir / f"candidate_{index:02d}.png"
        edit_id = submit_workflow(
            args.comfy_url,
            build_qwen_edit_workflow(
                image_names=edit_names,
                edit_prompt=edit_prompt,
                seed=seed + 101,
                filename_prefix=(
                    f"novel2anime/keyframes/{args.run_name}/candidate_{index:02d}"
                ),
                output_width=args.width,
                output_height=args.height,
            ),
            final_path,
            timeout_seconds=args.timeout_seconds,
        )
        qc = technical_qc(final_path, expected_size=(args.width, args.height))
        outputs.append(
            {
                "candidate": index,
                "seed": seed,
                "composition_file": base_path.name if base_path else "",
                "composition_prompt_id": base_id,
                "file": final_path.name,
                "identity_edit_prompt_id": edit_id,
                "technical_qc": qc,
                "approval_status": (
                    "pending_identity_composition_review"
                    if qc["technical_pass"]
                    else "rejected_technical"
                ),
            }
        )
        print(f"[PROGRESS] {index}/{args.candidate_count} keyframe complete", flush=True)
    manifest = _manifest(
        args=args,
        prompt=prompt,
        outputs=outputs,
        elapsed_seconds=time.monotonic() - started,
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--reference-image", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-name", default=f"keyframe_{uuid4().hex[:8]}")
    parser.add_argument("--frame-role", choices=("start", "end"), default="start")
    parser.add_argument("--candidate-count", type=int, default=2, choices=range(2, 5))
    parser.add_argument("--seed", type=int, default=2026082601)
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--base-steps", type=int, default=9, choices=range(8, 13))
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--comfy-root", default="/root/autodl-tmp/ComfyUI")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
