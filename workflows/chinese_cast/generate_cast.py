"""High-quality staged Chinese live-action cast generation for ComfyUI.

This workflow deliberately separates aesthetic casting from technical turnaround
generation.  Z-Image Turbo creates single-person casting candidates.  Only a
manually approved candidate may enter the Qwen-Image-Edit 2511 multi-angle stage.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

Z_IMAGE_MODEL = "z_image_turbo_int8_convrot.safetensors"
Z_IMAGE_TEXT_ENCODER = "qwen_3_4b_fp8_mixed.safetensors"
Z_IMAGE_VAE = "z_image_ae.safetensors"

QWEN_EDIT_MODEL = "qwen_image_edit_2511_int8_convrot.safetensors"
QWEN_EDIT_TEXT_ENCODER = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_EDIT_VAE = "qwen_image_vae.safetensors"
MULTI_ANGLE_LORA = "qwen-image-edit-2511-multiple-angles-lora.safetensors"

ANGLE_PROMPTS = {
    "front": (
        "将图中同一个人转为严格正面全身定妆照。人物头部、双肩、髋部和双脚正对镜头，"
        "左右对称站立，双臂自然离开躯干，双手清楚可见。保持脸型、五官、年龄、发际线、"
        "发型、服装结构、纹样、材质、配色、身材比例完全不变。纯浅灰无缝摄影棚背景，"
        "平视长焦镜头，均匀柔光，完整头顶和鞋底，不要文字、边框、道具或其他人物。"
    ),
    "left_profile": (
        "将图中同一个人原地旋转为严格90度左侧面全身定妆照。脸、双肩、髋部和双脚均精确"
        "朝向画面左侧，只能看到一只眼睛和一只耳朵，鼻梁形成清晰的纯侧面轮廓，绝不是"
        "四分之三侧面。保持脸部身份、年龄、发际线、发型、服装结构、纹样、材质、配色和"
        "身材比例完全不变。纯浅灰无缝摄影棚背景，平视长焦镜头，均匀柔光，完整头顶和"
        "鞋底，不要文字、边框、道具或其他人物。"
    ),
    "back": (
        "将图中同一个人原地旋转为严格背面全身定妆照。后脑、双肩、背部、髋部和双脚完全"
        "背对镜头，不得露出眼睛、鼻子、嘴唇、面颊或任何面部侧影。保持发型背面结构、服装"
        "层次、纹样、材质、配色、配饰和身材比例完全不变。纯浅灰无缝摄影棚背景，平视长焦"
        "镜头，均匀柔光，完整头顶和鞋底，不要文字、边框、道具或其他人物。"
    ),
}


def normalize_image_size(width: int, height: int) -> tuple[int, int]:
    """Return portrait dimensions accepted by the image latent nodes."""

    width = max(512, min(int(width), 2048))
    height = max(512, min(int(height), 2048))
    return width - width % 16, height - height % 16


def build_z_image_workflow(
    prompt: str,
    *,
    seed: int,
    width: int,
    height: int,
    filename_prefix: str,
    steps: int = 9,
) -> dict[str, dict[str, Any]]:
    """Build the API-format official Z-Image Turbo text-to-image graph."""

    width, height = normalize_image_size(width, height)
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": Z_IMAGE_MODEL, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": Z_IMAGE_TEXT_ENCODER,
                "type": "lumina2",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": Z_IMAGE_VAE},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": prompt},
        },
        "5": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["4", 0]},
        },
        "6": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "7": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["1", 0], "shift": 3.0},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["7", 0],
                "seed": seed,
                "steps": max(8, min(int(steps), 12)),
                "cfg": 1.0,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "denoise": 1.0,
            },
        },
        "9": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["8", 0], "vae": ["3", 0]},
        },
        "10": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["9", 0],
                "filename_prefix": filename_prefix,
            },
        },
    }


def build_qwen_angle_workflow(
    *,
    image_name: str,
    edit_prompt: str,
    seed: int,
    filename_prefix: str,
    lora_strength: float = 1.0,
    output_width: int = 0,
    output_height: int = 0,
) -> dict[str, dict[str, Any]]:
    """Build the official 40-step Qwen 2511 edit graph plus angle LoRA."""

    return build_qwen_edit_workflow(
        image_names=[image_name],
        edit_prompt=edit_prompt,
        seed=seed,
        filename_prefix=filename_prefix,
        lora_strength=lora_strength,
        output_width=output_width,
        output_height=output_height,
    )


def build_qwen_edit_workflow(
    *,
    image_names: list[str],
    edit_prompt: str,
    seed: int,
    filename_prefix: str,
    lora_strength: float = 0.0,
    output_width: int = 0,
    output_height: int = 0,
) -> dict[str, dict[str, Any]]:
    """Build an identity-aware Qwen edit graph using one to three references."""

    if not 1 <= len(image_names) <= 3:
        raise ValueError("Qwen edit requires one to three reference images")

    positive_inputs: dict[str, Any] = {
        "clip": ["7", 0],
        "vae": ["8", 0],
        "prompt": edit_prompt,
    }
    negative_inputs: dict[str, Any] = {
        "clip": ["7", 0],
        "vae": ["8", 0],
        "prompt": "",
    }
    workflow: dict[str, dict[str, Any]] = {}
    for index, image_name_value in enumerate(image_names, start=1):
        load_id = str(20 + index)
        scale_id = str(30 + index)
        workflow[load_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": image_name_value},
        }
        workflow[scale_id] = {
            "class_type": "FluxKontextImageScale",
            "inputs": {"image": [load_id, 0]},
        }
        positive_inputs[f"image{index}"] = [scale_id, 0]
        negative_inputs[f"image{index}"] = [scale_id, 0]

    model_source: list[Any] = ["3", 0]
    if lora_strength > 0:
        workflow["4"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["3", 0],
                "lora_name": MULTI_ANGLE_LORA,
                "strength_model": max(0.0, min(float(lora_strength), 1.5)),
            },
        }
        model_source = ["4", 0]

    output_image: list[Any] = ["15", 0]
    if output_width > 0 and output_height > 0:
        width = max(16, min(int(output_width), 4096))
        height = max(16, min(int(output_height), 4096))
        width -= width % 16
        height -= height % 16
        workflow["17"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["15", 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "disabled",
            },
        }
        output_image = ["17", 0]

    workflow.update(
        {
            "3": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": QWEN_EDIT_MODEL, "weight_dtype": "default"},
            },
            "5": {
                "class_type": "ModelSamplingAuraFlow",
                "inputs": {"model": model_source, "shift": 3.1},
            },
            "6": {
                "class_type": "CFGNorm",
                "inputs": {"model": ["5", 0], "strength": 1.0},
            },
            "7": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": QWEN_EDIT_TEXT_ENCODER,
                    "type": "qwen_image",
                    "device": "default",
                },
            },
            "8": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": QWEN_EDIT_VAE},
            },
            "9": {
                "class_type": "TextEncodeQwenImageEditPlus",
                "inputs": positive_inputs,
            },
            "10": {
                "class_type": "TextEncodeQwenImageEditPlus",
                "inputs": negative_inputs,
            },
            "11": {
                "class_type": "FluxKontextMultiReferenceLatentMethod",
                "inputs": {
                    "conditioning": ["9", 0],
                    "reference_latents_method": "index_timestep_zero",
                },
            },
            "12": {
                "class_type": "FluxKontextMultiReferenceLatentMethod",
                "inputs": {
                    "conditioning": ["10", 0],
                    "reference_latents_method": "index_timestep_zero",
                },
            },
            "13": {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["31", 0], "vae": ["8", 0]},
            },
            "14": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["6", 0],
                    "seed": seed,
                    "steps": 40,
                    "cfg": 4.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "positive": ["11", 0],
                    "negative": ["12", 0],
                    "latent_image": ["13", 0],
                    "denoise": 1.0,
                },
            },
            "15": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["14", 0], "vae": ["8", 0]},
            },
            "16": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": output_image,
                    "filename_prefix": filename_prefix,
                },
            },
        }
    )
    return workflow


def request_json(
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"ComfyUI HTTP {exc.code}: {detail}") from exc


def wait_for_image(
    base_url: str,
    prompt_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_transport_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            history = request_json(base_url, f"/history/{prompt_id}")
            last_transport_error = None
        except (TimeoutError, urllib.error.URLError) as exc:
            # Large quantized models can briefly starve ComfyUI's HTTP thread while
            # weights are staged. The prompt is still valid, so keep polling within
            # the caller's overall deadline instead of submitting a duplicate job.
            last_transport_error = exc
            time.sleep(3)
            continue
        record = history.get(prompt_id)
        if record:
            status = record.get("status") or {}
            if status.get("completed") and status.get("status_str") != "success":
                raise RuntimeError(f"ComfyUI task failed: {json.dumps(status, ensure_ascii=False)}")
            images = [
                image
                for output in (record.get("outputs") or {}).values()
                for image in output.get("images", [])
            ]
            if images:
                return images[-1]
        time.sleep(2)
    detail = f"; last transport error: {last_transport_error}" if last_transport_error else ""
    raise TimeoutError(f"ComfyUI image task timed out: {prompt_id}{detail}")


def release_comfy_memory(base_url: str) -> None:
    """Unload models retained by an earlier stage before loading Qwen Edit."""

    try:
        request_json(
            base_url,
            "/free",
            {"unload_models": True, "free_memory": True},
            timeout=60,
        )
    except (TimeoutError, urllib.error.URLError):
        # Memory release is an optimization. A clean ComfyUI process may not need
        # it, and generation still has its own bounded timeout and diagnostics.
        return


def download_image(base_url: str, image: dict[str, Any], destination: Path) -> None:
    query = urllib.parse.urlencode(
        {
            "filename": image["filename"],
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/view?{query}", timeout=180) as response:
        destination.write_bytes(response.read())


def submit_workflow(
    base_url: str,
    workflow: dict[str, dict[str, Any]],
    destination: Path,
    *,
    timeout_seconds: int,
) -> str:
    response = request_json(
        base_url,
        "/prompt",
        {"prompt": workflow, "client_id": f"novel2anime-{uuid4().hex}"},
    )
    prompt_id = str(response.get("prompt_id") or "")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {response}")
    image = wait_for_image(base_url, prompt_id, timeout_seconds=timeout_seconds)
    download_image(base_url, image, destination)
    return prompt_id


def technical_qc(path: Path, *, expected_size: tuple[int, int]) -> dict[str, Any]:
    """Record objective image health checks; aesthetics remain a human gate."""

    with Image.open(path) as source:
        image = source.convert("RGB")
        gray = image.convert("L")
        stats = ImageStat.Stat(gray)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_std = float(ImageStat.Stat(edges).stddev[0])
        histogram = gray.histogram()
        pixels = max(1, image.width * image.height)
        clipped_ratio = (sum(histogram[:4]) + sum(histogram[-4:])) / pixels
        checks = {
            "dimensions": image.size == expected_size,
            "file_size": path.stat().st_size >= 100_000,
            "tonal_range": float(stats.stddev[0]) >= 20.0,
            "edge_detail": edge_std >= 8.0,
            "limited_clipping": clipped_ratio <= 0.30,
        }
        return {
            "width": image.width,
            "height": image.height,
            "file_bytes": path.stat().st_size,
            "luma_mean": round(float(stats.mean[0]), 3),
            "luma_stddev": round(float(stats.stddev[0]), 3),
            "edge_stddev": round(edge_std, 3),
            "clipped_ratio": round(clipped_ratio, 6),
            "checks": checks,
            "technical_pass": all(checks.values()),
            "aesthetic_review": "required",
        }


def _fit_panel(image: Image.Image, panel_size: tuple[int, int]) -> Image.Image:
    panel = Image.new("RGB", panel_size, "#eeeeee")
    copy = image.copy()
    copy.thumbnail((panel_size[0] - 32, panel_size[1] - 64), Image.Resampling.LANCZOS)
    panel.paste(copy, ((panel.width - copy.width) // 2, 16))
    return panel


def make_contact_sheet(
    image_paths: list[Path],
    destination: Path,
    *,
    labels: list[str] | None = None,
) -> None:
    if not image_paths:
        raise ValueError("contact sheet needs at least one image")
    panel_size = (520, 760)
    sheet = Image.new("RGB", (panel_size[0] * len(image_paths), panel_size[1]), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, path in enumerate(image_paths):
        with Image.open(path) as source:
            panel = _fit_panel(source.convert("RGB"), panel_size)
        sheet.paste(panel, (index * panel_size[0], 0))
        label = labels[index] if labels else f"candidate {index + 1:02d}"
        draw.text((index * panel_size[0] + 16, panel_size[1] - 34), label, fill="black", font=font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, quality=96)


def _copy_comfy_input(comfy_root: Path, source: Path) -> str:
    name = f"novel2anime/cast/{uuid4().hex}{source.suffix.lower()}"
    destination = comfy_root / "input" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return name


def run_casting(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("casting prompt is empty")
    width, height = normalize_image_size(args.width, args.height)
    candidates: list[dict[str, Any]] = []
    paths: list[Path] = []
    started = time.monotonic()
    for index in range(1, args.candidate_count + 1):
        seed = args.seed + index * 9973
        path = output_dir / f"candidate_{index:02d}.png"
        prefix = f"novel2anime/cast/{args.run_name}/candidate_{index:02d}"
        workflow = build_z_image_workflow(
            prompt,
            seed=seed,
            width=width,
            height=height,
            filename_prefix=prefix,
            steps=args.steps,
        )
        prompt_id = submit_workflow(
            args.comfy_url,
            workflow,
            path,
            timeout_seconds=args.timeout_seconds,
        )
        paths.append(path)
        candidates.append(
            {
                "index": index,
                "seed": seed,
                "file": path.name,
                "prompt_id": prompt_id,
                "technical_qc": technical_qc(path, expected_size=(width, height)),
                "approval_status": "pending_human_review",
            }
        )
        print(f"[PROGRESS] {index}/{args.candidate_count} casting complete", flush=True)
    make_contact_sheet(paths, output_dir / "contact_sheet.png")
    manifest = {
        "schema_version": "2.0",
        "stage": "casting_candidates",
        "model": Z_IMAGE_MODEL,
        "quantized": True,
        "text_encoder": Z_IMAGE_TEXT_ENCODER,
        "vae": Z_IMAGE_VAE,
        "width": width,
        "height": height,
        "steps": args.steps,
        "prompt": prompt,
        "candidates": candidates,
        "gate": {
            "status": "blocked_until_human_selection",
            "selected_candidate": None,
            "next_stage": "qwen_2511_identity_locked_angles",
        },
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def run_angles(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source_image).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"approved source image does not exist: {source}")
    approval = Path(args.approval_file).resolve()
    if not approval.is_file():
        raise RuntimeError("multi-angle generation requires an explicit approval file")
    approval_data = json.loads(approval.read_text(encoding="utf-8"))
    if (
        approval_data.get("status") != "approved"
        or Path(str(approval_data.get("source_image") or "")).resolve() != source
    ):
        raise RuntimeError("approval file does not approve the selected source image")

    comfy_root = Path(args.comfy_root).resolve()
    with Image.open(source) as source_image:
        output_width, output_height = source_image.size
    image_name = _copy_comfy_input(comfy_root, source)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "angle_progress.json"
    existing_records: dict[str, dict[str, Any]] = {}
    if progress_path.is_file():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if progress.get("source_image") == str(source):
            existing_records = {
                str(record.get("file")): record
                for record in progress.get("outputs", [])
                if isinstance(record, dict) and record.get("file")
            }

    release_comfy_memory(args.comfy_url)
    records: list[dict[str, Any]] = []
    selected_paths: list[Path] = []
    started = time.monotonic()
    for angle_index, (angle, prompt) in enumerate(ANGLE_PROMPTS.items(), start=1):
        angle_candidates: list[Path] = []
        for candidate_index in range(1, args.candidate_count + 1):
            seed = args.seed + angle_index * 100_003 + candidate_index * 9973
            path = output_dir / f"{angle}_candidate_{candidate_index:02d}.png"
            existing = existing_records.get(path.name)
            if path.is_file():
                try:
                    with Image.open(path) as completed_image:
                        completed_image.verify()
                    records.append(
                        existing
                        or {
                            "angle": angle,
                            "candidate": candidate_index,
                            "seed": seed,
                            "file": path.name,
                            "prompt_id": "recovered_existing_output",
                            "approval_status": "pending_human_review",
                        }
                    )
                    angle_candidates.append(path)
                    print(f"[RESUME] keeping completed {path.name}", flush=True)
                    continue
                except OSError:
                    path.unlink(missing_ok=True)
            prefix = f"novel2anime/cast/{args.run_name}/{angle}_candidate_{candidate_index:02d}"
            workflow = build_qwen_angle_workflow(
                image_name=image_name,
                edit_prompt=prompt,
                seed=seed,
                filename_prefix=prefix,
                lora_strength=args.lora_strength,
                output_width=output_width,
                output_height=output_height,
            )
            prompt_id = submit_workflow(
                args.comfy_url,
                workflow,
                path,
                timeout_seconds=args.timeout_seconds,
            )
            angle_candidates.append(path)
            records.append(
                {
                    "angle": angle,
                    "candidate": candidate_index,
                    "seed": seed,
                    "file": path.name,
                    "prompt_id": prompt_id,
                    "approval_status": "pending_human_review",
                }
            )
            progress_path.write_text(
                json.dumps(
                    {
                        "source_image": str(source),
                        "outputs": records,
                        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            done = (angle_index - 1) * args.candidate_count + candidate_index
            total = len(ANGLE_PROMPTS) * args.candidate_count
            print(f"[PROGRESS] {done}/{total} angle complete", flush=True)
        selected_paths.append(angle_candidates[0])
    make_contact_sheet(
        selected_paths,
        output_dir / "angle_contact_sheet_preview.png",
        labels=list(ANGLE_PROMPTS),
    )
    manifest = {
        "schema_version": "2.0",
        "stage": "identity_locked_angles",
        "model": QWEN_EDIT_MODEL,
        "quantized": True,
        "text_encoder": QWEN_EDIT_TEXT_ENCODER,
        "vae": QWEN_EDIT_VAE,
        "lora": MULTI_ANGLE_LORA,
        "lora_strength": args.lora_strength,
        "source_image": str(source),
        "approval_file": str(approval),
        "outputs": records,
        "gate": {
            "status": "blocked_until_per_angle_human_selection",
            "next_stage": "compose_turnaround_and_identity_anchor",
        },
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    progress_path.unlink(missing_ok=True)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="stage", required=True)

    casting = subparsers.add_parser("casting")
    casting.add_argument("--prompt-file", required=True)
    casting.add_argument("--output-dir", required=True)
    casting.add_argument("--run-name", required=True)
    casting.add_argument("--candidate-count", type=int, default=4, choices=range(4, 9))
    casting.add_argument("--seed", type=int, default=2026082601)
    casting.add_argument("--width", type=int, default=896)
    casting.add_argument("--height", type=int, default=1344)
    casting.add_argument("--steps", type=int, default=9, choices=range(8, 13))
    casting.add_argument("--timeout-seconds", type=int, default=3600)
    casting.add_argument("--comfy-url", default="http://127.0.0.1:8188")

    angles = subparsers.add_parser("angles")
    angles.add_argument("--source-image", required=True)
    angles.add_argument("--approval-file", required=True)
    angles.add_argument("--output-dir", required=True)
    angles.add_argument("--run-name", required=True)
    angles.add_argument("--candidate-count", type=int, default=2, choices=range(2, 5))
    angles.add_argument("--seed", type=int, default=2026082601)
    angles.add_argument("--lora-strength", type=float, default=1.0)
    angles.add_argument("--timeout-seconds", type=int, default=7200)
    angles.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    angles.add_argument("--comfy-root", default="/root/autodl-tmp/ComfyUI")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.stage == "casting":
        run_casting(arguments)
    else:
        run_angles(arguments)
