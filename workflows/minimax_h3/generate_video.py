"""Submit a MiniMax H3 FL2VA audio-video job to local ComfyUI.

The desktop application uploads this self-contained script to the GPU server.
It uses ComfyUI's official MiniMax H3 nodes and the pruned INT8/NVFP4 model
combination intended for memory-constrained local inference.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

H3_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
H3_TEXT_ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
H3_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"


def probe_video(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_fps: int,
    expected_duration: float,
) -> dict:
    """Run objective container/stream checks before a candidate can be reviewed."""

    executable = shutil.which("ffprobe")
    if not executable:
        return {
            "technical_pass": False,
            "error": "ffprobe_not_found",
            "approval_status": "rejected_technical",
        }
    process = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if process.returncode:
        return {
            "technical_pass": False,
            "error": (process.stderr or "ffprobe_failed").strip()[:1000],
            "approval_status": "rejected_technical",
        }
    payload = json.loads(process.stdout)
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    rate = str(video.get("avg_frame_rate") or "0/1")
    numerator, _, denominator = rate.partition("/")
    fps = float(numerator or 0) / max(float(denominator or 1), 1.0)
    duration = float(video.get("duration") or (payload.get("format") or {}).get("duration") or 0.0)
    audio_duration = float(
        audio.get("duration") or (payload.get("format") or {}).get("duration") or 0.0
    )
    audio_sample_rate = int(audio.get("sample_rate") or 0)
    audio_channels = int(audio.get("channels") or 0)
    checks = {
        "has_video": bool(video),
        "has_audio": bool(audio),
        "width": int(video.get("width") or 0) == expected_width,
        "height": int(video.get("height") or 0) == expected_height,
        "fps": abs(fps - expected_fps) <= 0.05,
        "duration": abs(duration - expected_duration) <= max(0.30, 1 / expected_fps),
        "audio_duration": abs(audio_duration - expected_duration) <= max(0.50, 2 / expected_fps),
        "audio_sample_rate": audio_sample_rate >= 32_000,
        "audio_channels": audio_channels >= 1,
    }
    return {
        "video_codec": str(video.get("codec_name") or ""),
        "audio_codec": str(audio.get("codec_name") or ""),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 4),
        "duration_seconds": round(duration, 4),
        "audio_duration_seconds": round(audio_duration, 4),
        "audio_sample_rate": audio_sample_rate,
        "audio_channels": audio_channels,
        "checks": checks,
        "technical_pass": all(checks.values()),
        "approval_status": (
            "pending_visual_motion_audio_review" if all(checks.values()) else "rejected_technical"
        ),
    }


def create_video_qc_sheet(
    path: Path,
    destination: Path,
    *,
    frame_count: int,
) -> bool:
    """Create five evenly spaced review frames without altering the video."""

    executable = shutil.which("ffmpeg")
    if not executable:
        return False
    last = max(0, frame_count - 1)
    indices = sorted({round(last * ratio / 4) for ratio in range(5)})
    expression = "+".join(f"eq(n\\,{index})" for index in indices)
    process = subprocess.run(
        [
            executable,
            "-y",
            "-i",
            str(path),
            "-vf",
            f"select={expression},scale=416:-2,tile={len(indices)}x1",
            "-frames:v",
            "1",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return process.returncode == 0 and destination.is_file()


def normalize_frame_count(value: int) -> int:
    """Round up to MiniMax H3's 17k+5 frame grid."""

    requested = max(5, int(value))
    return requested + (5 - requested % 17) % 17


def build_prompt(
    *,
    image_name: str,
    end_image_name: str = "",
    positive_prompt: str,
    width: int,
    height: int,
    frame_count: int,
    fps: int,
    seed: int,
    filename_prefix: str,
) -> dict[str, dict]:
    """Build the API-format equivalent of ComfyUI's official H3 I2V graph."""

    h3_inputs: dict[str, object] = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "prompt": positive_prompt,
        "width": width,
        "height": height,
        "length": normalize_frame_count(frame_count),
        "first_frame": ["5", 0],
    }
    if end_image_name:
        h3_inputs["last_frame"] = ["6", 0]

    prompt: dict[str, dict] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": H3_MODEL,
                "weight_dtype": "default",
            },
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": H3_TEXT_ENCODER,
                "type": "minimax",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": H3_VIDEO_VAE},
        },
        "4": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": H3_AUDIO_VAE},
        },
        "5": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
        },
        "7": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": h3_inputs,
        },
        "8": {
            "class_type": "BasicGuider",
            "inputs": {
                "model": ["1", 0],
                "conditioning": ["7", 0],
            },
        },
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["1", 0],
                "scheduler": "simple",
                "steps": 20,
                "denoise": 1.0,
            },
        },
        "10": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "res_multistep"},
        },
        "11": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": seed},
        },
        "12": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["11", 0],
                "guider": ["8", 0],
                "sampler": ["10", 0],
                "sigmas": ["9", 0],
                "latent_image": ["7", 1],
            },
        },
        "13": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["12", 0], "vae": ["3", 0]},
        },
        "14": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["12", 0], "vae": ["4", 0]},
        },
        "15": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["13", 0],
                "audio": ["14", 0],
                "fps": fps,
            },
        },
        "16": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["15", 0],
                "filename_prefix": filename_prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }
    if end_image_name:
        prompt["6"] = {
            "class_type": "LoadImage",
            "inputs": {"image": end_image_name},
        }
    return prompt


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
    last_transport_error: Exception | None = None
    while time.monotonic() - started < timeout_seconds:
        try:
            history = request_json(base_url, f"/history/{prompt_id}", timeout=30)
            last_transport_error = None
        except (TimeoutError, urllib.error.URLError) as exc:
            last_transport_error = exc
            time.sleep(3)
            continue
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
    detail = f"；最近一次连接错误：{last_transport_error}" if last_transport_error else ""
    raise TimeoutError(f"ComfyUI 任务超时：{prompt_id}{detail}")


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
        raise FileNotFoundError(f"ComfyUI 已完成，但未找到视频输出：{directory}/{relative.name}_*")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _copy_input(comfy_root: Path, source: Path) -> str:
    input_name = f"novel2anime/{uuid4().hex}{source.suffix.lower()}"
    destination = comfy_root / "input" / input_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return input_name


def run(args: argparse.Namespace) -> dict:
    comfy_root = Path(args.comfy_root).resolve()
    source = Path(args.source_image).resolve()
    end_source = Path(args.end_image).resolve() if args.end_image else None
    output_dir = Path(args.output_dir).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"起始帧不存在：{source}")
    if end_source is not None and not end_source.is_file():
        raise FileNotFoundError(f"结束帧不存在：{end_source}")
    positive_prompt = args.positive_prompt
    if args.positive_prompt_file:
        positive_prompt = Path(args.positive_prompt_file).read_text(encoding="utf-8")
    if not positive_prompt.strip():
        raise ValueError("MiniMax H3 positive prompt is empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    input_name = _copy_input(comfy_root, source)
    end_input_name = _copy_input(comfy_root, end_source) if end_source else ""

    outputs: list[dict] = []
    started = time.monotonic()
    for index in range(args.candidate_count):
        candidate_seed = args.seed + index
        prefix = f"novel2anime/{args.run_name}/candidate_{index + 1:02d}"
        prompt = build_prompt(
            image_name=input_name,
            end_image_name=end_input_name,
            positive_prompt=positive_prompt,
            width=args.width,
            height=args.height,
            frame_count=args.frame_count,
            fps=args.fps,
            seed=candidate_seed,
            filename_prefix=prefix,
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
        normalized_length = normalize_frame_count(args.frame_count)
        expected_duration = normalized_length / args.fps
        technical_qc = probe_video(
            destination,
            expected_width=args.width,
            expected_height=args.height,
            expected_fps=args.fps,
            expected_duration=expected_duration,
        )
        qc_sheet = output_dir / f"candidate_{index + 1:02d}_qc.jpg"
        qc_sheet_created = create_video_qc_sheet(
            destination,
            qc_sheet,
            frame_count=normalized_length,
        )
        outputs.append(
            {
                "candidate_index": index + 1,
                "seed": candidate_seed,
                "prompt_id": prompt_id,
                "file": destination.name,
                "technical_qc": technical_qc,
                "qc_contact_sheet": qc_sheet.name if qc_sheet_created else "",
                "approval_status": technical_qc["approval_status"],
            }
        )
        print(
            f"[PROGRESS] {index + 1}/{args.candidate_count} complete {destination.name}",
            flush=True,
        )

    manifest = {
        "schema_version": "1.0",
        "engine_profile": "minimax_h3_fl2va",
        "model": H3_MODEL,
        "text_encoder": H3_TEXT_ENCODER,
        "video_vae": H3_VIDEO_VAE,
        "audio_vae": H3_AUDIO_VAE,
        "source_image": str(source),
        "end_image": str(end_source) if end_source else "",
        "width": args.width,
        "height": args.height,
        "frame_count": normalize_frame_count(args.frame_count),
        "fps": args.fps,
        "positive_prompt": positive_prompt,
        "native_audio": True,
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
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--positive-prompt", default="")
    parser.add_argument("--positive-prompt-file", default="")
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frame-count", type=int, default=124)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--candidate-count", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=14400)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--comfy-root", default="/root/autodl-tmp/ComfyUI")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
