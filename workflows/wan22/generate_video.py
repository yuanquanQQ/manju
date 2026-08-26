"""Submit a Wan2.2 TI2V 5B or 14B FLF2V job to local ComfyUI.

This script is intentionally self-contained so the desktop application can
upload it to the GPU server and execute it without installing project
dependencies there.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

DEFAULT_NEGATIVE_PROMPT = (
    "face morphing, identity drift, deformed hands, extra fingers, extra limbs, "
    "duplicate person, flicker, jitter, camera shake, warped background, text, "
    "subtitle, watermark, blurry, low quality, frozen frame"
)

TI2V_MODEL = "wan2.2_ti2v_5B_fp16.safetensors"
TI2V_VAE = "wan2.2_vae.safetensors"
FLF_HIGH_MODEL = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
FLF_LOW_MODEL = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
FLF_VAE = "wan_2.1_vae.safetensors"
TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"


def normalize_frame_count(value: int) -> int:
    """Wan video lengths use the 4n+1 sequence."""

    requested = max(5, int(value))
    return max(5, round((requested - 1) / 4) * 4 + 1)


def build_prompt(
    *,
    image_name: str,
    positive_prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    frame_count: int,
    fps: int,
    seed: int,
    filename_prefix: str,
    engine_profile: str = "wan22_ti2v_5b",
    end_image_name: str = "",
) -> dict[str, dict]:
    """Build an official native Wan2.2 API-format workflow."""

    if engine_profile == "wan22_flf2v":
        if not end_image_name:
            raise ValueError("Wan2.2 FLF2V requires an end image")
        return build_flf_prompt(
            image_name=image_name,
            end_image_name=end_image_name,
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            frame_count=frame_count,
            fps=fps,
            seed=seed,
            filename_prefix=filename_prefix,
        )
    if engine_profile != "wan22_ti2v_5b":
        raise ValueError(f"Unsupported Wan engine profile: {engine_profile}")

    return {
        "37": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": TI2V_MODEL,
                "weight_dtype": "default",
            },
        },
        "38": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": TEXT_ENCODER,
                "type": "wan",
                "device": "default",
            },
        },
        "39": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": TI2V_VAE},
        },
        "56": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
        },
        "55": {
            "class_type": "Wan22ImageToVideoLatent",
            "inputs": {
                "vae": ["39", 0],
                "start_image": ["56", 0],
                "width": width,
                "height": height,
                "length": normalize_frame_count(frame_count),
                "batch_size": 1,
            },
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": positive_prompt,
                "clip": ["38", 0],
            },
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_prompt or DEFAULT_NEGATIVE_PROMPT,
                "clip": ["38", 0],
            },
        },
        "48": {
            "class_type": "ModelSamplingSD3",
            "inputs": {
                "model": ["37", 0],
                "shift": 8,
            },
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["48", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["55", 0],
                "seed": seed,
                "steps": 20,
                "cfg": 5.0,
                "sampler_name": "uni_pc",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["39", 0],
            },
        },
        "57": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["8", 0],
                "fps": fps,
            },
        },
        "58": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["57", 0],
                "filename_prefix": filename_prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }


def build_flf_prompt(
    *,
    image_name: str,
    end_image_name: str,
    positive_prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    frame_count: int,
    fps: int,
    seed: int,
    filename_prefix: str,
) -> dict[str, dict]:
    """Build ComfyUI's official Wan2.2 14B first/last-frame graph."""

    return {
        "10": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": FLF_HIGH_MODEL, "weight_dtype": "default"},
        },
        "11": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": FLF_LOW_MODEL, "weight_dtype": "default"},
        },
        "12": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": TEXT_ENCODER,
                "type": "wan",
                "device": "default",
            },
        },
        "13": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": FLF_VAE},
        },
        "14": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "15": {"class_type": "LoadImage", "inputs": {"image": end_image_name}},
        "16": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive_prompt, "clip": ["12", 0]},
        },
        "17": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_prompt or DEFAULT_NEGATIVE_PROMPT,
                "clip": ["12", 0],
            },
        },
        "18": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["10", 0], "shift": 8},
        },
        "19": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["11", 0], "shift": 8},
        },
        "20": {
            "class_type": "WanFirstLastFrameToVideo",
            "inputs": {
                "positive": ["16", 0],
                "negative": ["17", 0],
                "vae": ["13", 0],
                "width": width,
                "height": height,
                "length": normalize_frame_count(frame_count),
                "batch_size": 1,
                "start_image": ["14", 0],
                "end_image": ["15", 0],
            },
        },
        "21": {
            "class_type": "KSamplerAdvanced",
            "inputs": {
                "model": ["18", 0],
                "positive": ["20", 0],
                "negative": ["20", 1],
                "latent_image": ["20", 2],
                "add_noise": "enable",
                "noise_seed": seed,
                "steps": 20,
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "start_at_step": 0,
                "end_at_step": 10,
                "return_with_leftover_noise": "enable",
            },
        },
        "22": {
            "class_type": "KSamplerAdvanced",
            "inputs": {
                "model": ["19", 0],
                "positive": ["20", 0],
                "negative": ["20", 1],
                "latent_image": ["21", 0],
                "add_noise": "disable",
                "noise_seed": 0,
                "steps": 20,
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "start_at_step": 10,
                "end_at_step": 10000,
                "return_with_leftover_noise": "disable",
            },
        },
        "23": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["22", 0], "vae": ["13", 0]},
        },
        "24": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["23", 0], "fps": fps},
        },
        "25": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["24", 0],
                "filename_prefix": filename_prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }


def request_json(
    base_url: str,
    path: str,
    payload: dict | None = None,
    *,
    timeout: float = 30,
) -> dict:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"ComfyUI HTTP {exc.code}: {detail}") from exc


def wait_for_prompt(
    base_url: str,
    prompt_id: str,
    *,
    timeout_seconds: int,
) -> dict:
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        history = request_json(base_url, f"/history/{prompt_id}", timeout=30)
        record = history.get(prompt_id)
        if record:
            status = record.get("status") or {}
            if status.get("completed"):
                if status.get("status_str") != "success":
                    raise RuntimeError(
                        f"ComfyUI 任务失败：{json.dumps(status, ensure_ascii=False)}"
                    )
                return record
        time.sleep(2)
    raise TimeoutError(f"ComfyUI 任务超时：{prompt_id}")


def find_generated_video(
    comfy_root: Path,
    filename_prefix: str,
    *,
    submitted_at: float,
) -> Path:
    relative = Path(filename_prefix)
    directory = comfy_root / "output" / relative.parent
    candidates = [
        path
        for suffix in ("*.mp4", "*.webm", "*.mkv", "*.mov")
        for path in directory.glob(f"{relative.name}_*{suffix[1:]}")
        if path.stat().st_mtime >= submitted_at - 2
    ]
    if not candidates:
        raise FileNotFoundError(
            f"ComfyUI 已完成，但未找到视频输出：{directory}/{relative.name}_*"
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def run(args: argparse.Namespace) -> dict:
    comfy_root = Path(args.comfy_root).resolve()
    source = Path(args.source_image).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"起始帧不存在：{source}")
    end_source: Path | None = None
    if args.engine_profile == "wan22_flf2v":
        if not args.end_image:
            raise ValueError("Wan2.2 FLF2V 必须指定结束帧")
        end_source = Path(args.end_image).resolve()
        if not end_source.is_file():
            raise FileNotFoundError(f"结束帧不存在：{end_source}")
    input_dir = comfy_root / "input" / "novel2anime"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    input_name = f"novel2anime/{uuid4().hex}{source.suffix.lower()}"
    shutil.copy2(source, comfy_root / "input" / input_name)
    end_input_name = ""
    if end_source is not None:
        end_input_name = (
            f"novel2anime/{uuid4().hex}{end_source.suffix.lower()}"
        )
        shutil.copy2(end_source, comfy_root / "input" / end_input_name)

    outputs: list[dict] = []
    started = time.monotonic()
    for index in range(args.candidate_count):
        candidate_seed = args.seed + index
        prefix = f"novel2anime/{args.run_name}/candidate_{index + 1:02d}"
        prompt = build_prompt(
            image_name=input_name,
            positive_prompt=args.positive_prompt,
            negative_prompt=args.negative_prompt,
            width=args.width,
            height=args.height,
            frame_count=args.frame_count,
            fps=args.fps,
            seed=candidate_seed,
            filename_prefix=prefix,
            engine_profile=args.engine_profile,
            end_image_name=end_input_name,
        )
        submitted_at = time.time()
        response = request_json(
            args.comfy_url,
            "/prompt",
            {"prompt": prompt, "client_id": f"novel2anime-{uuid4().hex}"},
            timeout=30,
        )
        prompt_id = str(response.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 未返回 prompt_id：{response}")
        print(
            f"[PROGRESS] {index + 1}/{args.candidate_count} queued {prompt_id}",
            flush=True,
        )
        wait_for_prompt(
            args.comfy_url,
            prompt_id,
            timeout_seconds=args.timeout_seconds,
        )
        generated = find_generated_video(
            comfy_root,
            prefix,
            submitted_at=submitted_at,
        )
        destination = output_dir / f"candidate_{index + 1:02d}{generated.suffix.lower()}"
        shutil.copy2(generated, destination)
        outputs.append(
            {
                "candidate_index": index + 1,
                "seed": candidate_seed,
                "prompt_id": prompt_id,
                "file": destination.name,
            }
        )
        print(
            f"[PROGRESS] {index + 1}/{args.candidate_count} complete {destination.name}",
            flush=True,
        )

    manifest = {
        "schema_version": "1.0",
        "engine_profile": args.engine_profile,
        "model": (
            [FLF_HIGH_MODEL, FLF_LOW_MODEL]
            if args.engine_profile == "wan22_flf2v"
            else TI2V_MODEL
        ),
        "text_encoder": TEXT_ENCODER,
        "vae": FLF_VAE if args.engine_profile == "wan22_flf2v" else TI2V_VAE,
        "source_image": str(source),
        "end_image": str(end_source) if end_source else "",
        "width": args.width,
        "height": args.height,
        "frame_count": normalize_frame_count(args.frame_count),
        "fps": args.fps,
        "positive_prompt": args.positive_prompt,
        "negative_prompt": args.negative_prompt or DEFAULT_NEGATIVE_PROMPT,
        "outputs": outputs,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("[PROGRESS] done", flush=True)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--end-image", default="")
    parser.add_argument(
        "--engine-profile",
        choices=("wan22_ti2v_5b", "wan22_flf2v"),
        default="wan22_ti2v_5b",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--positive-prompt", required=True)
    parser.add_argument("--negative-prompt", default="")
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frame-count", type=int, default=81)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--candidate-count", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--comfy-root", default="/root/autodl-tmp/ComfyUI")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
