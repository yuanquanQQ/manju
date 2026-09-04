"""RTX 3090 server operations used by the desktop application."""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import paramiko

from app.core.config import settings
from app.core.files import atomic_write_json, sha256_text
from app.database.db import init_db
from app.domain.jobs import JobStatus
from app.domain.video import VideoArtifactMetadata, VideoRenderSpec
from app.services.artifact_service import register_artifact
from app.services.image_models import (
    IMAGE_MODEL_PRESETS,
    validate_image_model_ids,
)
from app.services.job_service import (
    create_job,
    heartbeat_job,
    transition_job,
)
from app.services.video_service import (
    VideoBatchResult,
    VideoClipResult,
    VideoRenderService,
)


# MiniMax H3 workflow engine. The T8 node graph (comfyui-minimax-h3-audio-T8)
# is the production engine: joint audio conditioning + dual-clock sampling.
# The stock official graph stays available for rollback through the
# generate_video.py --engine flag, but the desktop path always drives T8.
H3_WORKFLOW_ENGINE = "t8"
H3_T8_SAMPLER_NAME = "dual_clock_euler"
H3_T8_SCHEDULER = "native_flow"
H3_T8_SHIFT_VIDEO = 12.0
H3_T8_SHIFT_AUDIO = 3.0
H3_GENERATION_REVISION = "h3_t8_chained_v1"


@dataclass(slots=True)
class GpuConnection:
    host: str = "connect.nmb2.seetacloud.com"
    port: int = 25518
    username: str = "root"
    password: str = ""


@dataclass(slots=True)
class GpuStatus:
    ssh_online: bool = False
    comfy_online: bool = False
    gpu_name: str = "未连接"
    memory_used_mb: int = 0
    memory_total_mb: int = 0
    utilization_percent: int = 0
    disk_available: str = "—"
    krea_ready: bool = False
    available_model_ids: list[str] = field(default_factory=list)
    identity_adapter_ready: bool = False
    kontext_model_ready: bool = False
    kontext_runtime_ready: bool = False
    kontext_model_name: str = ""
    h3_model_ready: bool = False
    h3_runtime_ready: bool = False
    h3_model_name: str = ""
    t8_runtime_ready: bool = False
    message: str = ""


@dataclass(slots=True)
class GenerationResult:
    local_dir: Path
    images: list[Path]
    manifest: dict
    remote_log: str
    elapsed_seconds: float = 0


CAST_REFERENCE_DENOISE = 0.68


def _shot_character_names(shot: dict[str, Any]) -> list[str]:
    characters = shot.get("characters")
    names = [
        str(item.get("name") or "").strip()
        for item in (characters if isinstance(characters, list) else [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if not names:
        continuity = shot.get("continuity_plan")
        continuity = continuity if isinstance(continuity, dict) else {}
        names.extend(
            part.strip()
            for part in str(continuity.get("cast_signature") or "").split("|")
            if part.strip()
        )
    return list(dict.fromkeys(names))


def _cast_reference_character(shot: dict[str, Any]) -> str:
    """Choose the one cast portrait that best anchors this shot.

    A single portrait cannot lock every face in a multi-character frame. Prefer the
    speaking on-screen character and otherwise use the first character established by
    the storyboard. The image workflow decides whether to consume the portrait as an
    identity reference, img2img input, or prompt-only metadata.
    """

    names = _shot_character_names(shot)
    if not names:
        return ""
    audio = shot.get("audio_generation")
    audio = audio if isinstance(audio, dict) else {}
    speaker = str(audio.get("speaker") or "").strip()
    return speaker if speaker in names else names[0]


def _resolve_cast_reference(
    project_root: Path,
    cast_selections: dict[str, str],
    shot: dict[str, Any],
) -> tuple[str, Path] | None:
    cast_name = _cast_reference_character(shot)
    configured = cast_selections.get(cast_name)
    if not cast_name or not configured:
        return None
    candidate = (project_root / configured).resolve()
    if not candidate.is_relative_to(project_root) or not candidate.is_file():
        return None
    return cast_name, candidate


class GpuServerService:
    remote_project_root = "/root/autodl-tmp/manju"
    remote_comfy_root = "/root/autodl-tmp/ComfyUI"

    def check_status(self, config: GpuConnection) -> GpuStatus:
        status = GpuStatus()
        try:
            client = self._connect(config)
        except Exception as exc:
            status.message = self._friendly_error(exc)
            return status

        status.ssh_online = True
        command = r"""
gpu=$(nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)
disk=$(df -h /root/autodl-tmp 2>/dev/null | awk 'NR==2 {print $4}')
if curl -fsS --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then comfy=1; else comfy=0; fi
models=/root/autodl-tmp/ComfyUI/models
if [ -f "$models/diffusion_models/flux1-krea-dev_fp8_scaled.safetensors" ] &&
   [ -f "$models/text_encoders/clip_l.safetensors" ] &&
   [ -f "$models/text_encoders/t5xxl_fp8_e4m3fn.safetensors" ] &&
   [ -f "$models/vae/ae.safetensors" ]; then krea=1; else krea=0; fi
if [ -f "$models/checkpoints/Juggernaut_XI/Juggernaut-XI-byRunDiffusion.safetensors" ];
then juggernaut=1; else juggernaut=0; fi
kontext_model="$models/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"
if [ -f "$kontext_model" ] &&
   [ -f "$models/text_encoders/clip_l.safetensors" ] &&
   [ -f "$models/text_encoders/t5xxl_fp8_e4m3fn.safetensors" ] &&
   [ -f "$models/vae/ae.safetensors" ]; then kontext_ready=1; else kontext_ready=0; fi
available=""
if [ "$krea" = 1 ]; then available="flux_krea"; fi
if [ "$juggernaut" = 1 ]; then available="$available juggernaut_xi"; fi
h3_model="$models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"
h3_text="$models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
h3_video_vae="$models/vae/minimax_h3_video_vae_fp16.safetensors"
h3_audio_vae="$models/vae/minimax_h3_audio_vae_fp32.safetensors"
if [ -f "$h3_model" ] && [ -f "$h3_text" ] &&
   [ -f "$h3_video_vae" ] && [ -f "$h3_audio_vae" ];
then h3_ready=1; else h3_ready=0; fi
h3_nodes_ready=0
kontext_nodes_ready=0
identity_ready=0
t8_nodes_ready=0
if [ "$comfy" = 1 ]; then
  object_info=$(curl -fsS --max-time 8 http://127.0.0.1:8188/object_info 2>/dev/null || true)
  if printf '%s' "$object_info" | grep -q '"MiniMaxH3ImageToVideo"' &&
     printf '%s' "$object_info" | grep -q '"VAEDecodeAudio"' &&
     printf '%s' "$object_info" | grep -q '"CreateVideo"' &&
     printf '%s' "$object_info" | grep -q '"SaveVideo"'; then h3_nodes_ready=1; fi
  if printf '%s' "$object_info" | grep -q '"FluxKontextImageScale"' &&
     printf '%s' "$object_info" | grep -q '"ReferenceLatent"' &&
     printf '%s' "$object_info" | grep -q '"ConditioningZeroOut"'; then kontext_nodes_ready=1; fi
  if [ -f "$models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" ] &&
     [ -f "$models/ipadapter/ip-adapter-plus-face_sdxl_vit-h.safetensors" ] &&
     printf '%s' "$object_info" | grep -q '"IPAdapterUnifiedLoader"' &&
     printf '%s' "$object_info" | grep -q '"IPAdapter"'; then identity_ready=1; fi
  if printf '%s' "$object_info" | grep -q '"MiniMaxH3AudioConditioningT8"' &&
     printf '%s' "$object_info" | grep -q '"MiniMaxH3DualClockSamplerT8"' &&
     printf '%s' "$object_info" | grep -q '"MiniMaxH3AVDecodeT8"' &&
     printf '%s' "$object_info" | grep -q '"MiniMaxH3AudioMixT8"' &&
     printf '%s' "$object_info" | grep -q '"MiniMaxH3OutputTrimT8"'; then t8_nodes_ready=1; fi
fi
printf '%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n' \
  "$gpu" "$disk" "$comfy" "$krea" "$available" "$identity_ready" "$h3_ready" \
  "$h3_nodes_ready" "$(basename "$h3_model" 2>/dev/null)" "$kontext_ready" \
  "$kontext_nodes_ready" "$(basename "$kontext_model" 2>/dev/null)" "$t8_nodes_ready"
"""
        try:
            output = self._exec(client, command, timeout=20).splitlines()
            gpu = [part.strip() for part in (output[0] if output else "").split(",")]
            if len(gpu) >= 4:
                status.gpu_name = gpu[0]
                status.memory_used_mb = self._to_int(gpu[1])
                status.memory_total_mb = self._to_int(gpu[2])
                status.utilization_percent = self._to_int(gpu[3])
            status.disk_available = output[1] if len(output) > 1 else "—"
            status.comfy_online = len(output) > 2 and output[2] == "1"
            status.krea_ready = len(output) > 3 and output[3] == "1"
            status.available_model_ids = output[4].split() if len(output) > 4 else []
            status.identity_adapter_ready = len(output) > 5 and output[5] == "1"
            status.h3_model_ready = len(output) > 6 and output[6] == "1"
            status.h3_runtime_ready = (
                status.h3_model_ready and len(output) > 7 and output[7] == "1"
            )
            status.h3_model_name = output[8] if len(output) > 8 else ""
            status.kontext_model_ready = len(output) > 9 and output[9] == "1"
            status.kontext_runtime_ready = (
                status.kontext_model_ready and len(output) > 10 and output[10] == "1"
            )
            status.kontext_model_name = output[11] if len(output) > 11 else ""
            status.t8_runtime_ready = (
                status.h3_runtime_ready and len(output) > 12 and output[12] == "1"
            )
            status.message = "服务器连接正常"
        finally:
            client.close()
        return status

    def start_comfy(self, config: GpuConnection) -> GpuStatus:
        client = self._connect(config)
        try:
            self._ensure_remote_comfy(client)
        finally:
            client.close()
        return self.check_status(config)

    def install_identity_adapter(
        self,
        config: GpuConnection,
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> GpuStatus:
        """Install the SDXL IP-Adapter face weights used by locked cast images."""

        script = settings.project_root / "scripts" / "gpu" / "ipadapter" / "install.sh"
        if not script.is_file():
            raise FileNotFoundError(f"人脸身份参考安装脚本不存在: {script}")

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        report(2, "正在连接 GPU 服务器")
        client = self._connect(config)
        remote_dir = f"{self.remote_project_root}/scripts/gpu/ipadapter"
        remote_script = f"{remote_dir}/install.sh"
        try:
            self._exec(client, f"mkdir -p {shlex.quote(remote_dir)}", timeout=15)
            sftp = client.open_sftp()
            try:
                sftp.put(str(script), remote_script)
            finally:
                sftp.close()
            self._exec(
                client,
                f"chmod +x {shlex.quote(remote_script)}",
                timeout=15,
            )
            report(5, "正在通过 HF 镜像下载人脸身份参考权重")

            def on_output(line: str) -> None:
                match = re.search(r"\s(\d+)%", line)
                if match:
                    report(5 + int(int(match.group(1)) * 0.9), "人脸身份参考权重下载中")

            self._exec_streaming(
                client,
                (f"env HF_ENDPOINT=https://hf-mirror.com {shlex.quote(remote_script)}"),
                timeout=10800,
                output_callback=on_output,
            )
        finally:
            client.close()
        status = self.check_status(config)
        if not status.identity_adapter_ready:
            raise RuntimeError("人脸身份参考权重已下载，但 ComfyUI 节点检测未通过")
        report(100, "人脸身份参考已就绪")
        return status

    def install_minimax_h3(
        self,
        config: GpuConnection,
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> GpuStatus:
        """Update ComfyUI and install the minimal H3 FL2VA native-audio stack."""

        script_root = settings.project_root / "scripts" / "gpu" / "minimax_h3"
        script_names = ("update_comfyui.sh", "install.sh")
        missing = [name for name in script_names if not (script_root / name).is_file()]
        if missing:
            raise FileNotFoundError(f"H3 部署文件缺失：{', '.join(missing)}")

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        report(2, "正在连接 GPU 服务器")
        client = self._connect(config)
        remote_dir = f"{self.remote_project_root}/scripts/gpu/minimax_h3"
        try:
            self._exec(client, f"mkdir -p {shlex.quote(remote_dir)}", timeout=15)
            sftp = client.open_sftp()
            try:
                for name in script_names:
                    sftp.put(str(script_root / name), f"{remote_dir}/{name}")
            finally:
                sftp.close()
            self._exec(
                client,
                f"chmod +x {shlex.quote(remote_dir)}/*.sh",
                timeout=15,
            )

            report(5, "正在安全更新 ComfyUI（会保留旧提交号）")
            self._exec(
                client,
                (
                    "pkill -f '[p]ython.*main.py.*8188' >/dev/null 2>&1 || true; "
                    f"{shlex.quote(remote_dir + '/update_comfyui.sh')}"
                ),
                timeout=3600,
            )

            def on_output(line: str) -> None:
                if "minimax_h3_fl2va" in line:
                    report(12, "正在下载 H3 视频生成模型（约 19.5GiB）")
                elif "qwen3vl_32b" in line:
                    report(47, "正在下载 H3 文本编码器（约 14.6GiB）")
                elif "video_vae" in line:
                    report(74, "正在下载 H3 视频 VAE（约 4.9GiB）")
                elif "audio_vae" in line:
                    report(88, "正在下载 H3 音频 VAE（约 0.6GiB）")
                elif "[H3_STAGE] models_ready" in line:
                    report(94, "H3 模型文件已完成校验")

            report(10, "正在通过 HF 镜像断点续传 H3 模型（约 40GiB）")
            self._exec_streaming(
                client,
                (
                    "env HF_ENDPOINT=https://hf-mirror.com "
                    f"{shlex.quote(remote_dir + '/install.sh')}"
                ),
                timeout=43200,
                output_callback=on_output,
            )
        finally:
            client.close()

        report(96, "模型已下载，正在启动 ComfyUI 并验证 H3 节点")
        status = self.start_comfy(config)
        if not status.h3_model_ready:
            raise RuntimeError("H3 模型下载完成，但四个必需文件检测未通过")
        if not status.h3_runtime_ready:
            raise RuntimeError("H3 模型已安装，但 ComfyUI 原生 H3 音视频节点检测未通过")
        report(100, "MiniMax H3 FL2VA 已安装并可调用")
        return status

    def install_minimax_h3_t8(
        self,
        config: GpuConnection,
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> GpuStatus:
        """Install the MiniMax H3 Audio T8 custom-node bundle (offline tar.gz)."""

        script_root = settings.project_root / "scripts" / "gpu" / "minimax_h3_t8"
        script = script_root / "install.sh"
        archive = script_root / "comfyui-minimax-h3-audio-T8-main.tar.gz"
        missing = [
            str(path)
            for path in (script, archive)
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(f"T8 部署文件缺失：{'、'.join(missing)}")

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        report(2, "正在连接 GPU 服务器")
        client = self._connect(config)
        remote_dir = f"{self.remote_project_root}/scripts/gpu/minimax_h3_t8"
        remote_script = f"{remote_dir}/install.sh"
        remote_archive = f"{remote_dir}/{archive.name}"
        try:
            self._exec(client, f"mkdir -p {shlex.quote(remote_dir)}", timeout=15)
            sftp = client.open_sftp()
            try:
                sftp.put(str(script), remote_script)
                sftp.put(str(archive), remote_archive)
            finally:
                sftp.close()
            self._exec(
                client,
                f"chmod +x {shlex.quote(remote_script)}",
                timeout=15,
            )

            def on_output(line: str) -> None:
                if "t8_ready" in line:
                    report(90, "T8 音频增强包已写入 custom_nodes")

            report(5, "正在解压 T8 音频增强包（纯代码，无额外依赖）")
            self._exec_streaming(
                client,
                (
                    "env T8_ARCHIVE=" + shlex.quote(remote_archive)
                    + " " + shlex.quote(remote_script)
                ),
                timeout=600,
                output_callback=on_output,
            )
        finally:
            client.close()

        report(92, "正在重启 ComfyUI 并验证 T8 节点")
        status = self.start_comfy(config)
        if not status.t8_runtime_ready:
            raise RuntimeError(
                "T8 包已部署，但 ComfyUI 的五个核心 T8 音视频节点检测未通过；"
                "请确认远端 ComfyUI 已升级到支持 H3 原生协议的最新版。"
            )
        report(100, "MiniMax H3 Audio T8 已安装并可调用")
        return status

    def install_flux_kontext(
        self,
        config: GpuConnection,
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> GpuStatus:
        """Install the official FLUX.1 Kontext FP8 reference-image editor."""

        script = settings.project_root / "scripts" / "gpu" / "flux_kontext" / "install.sh"
        if not script.is_file():
            raise FileNotFoundError(f"Kontext 部署文件缺失：{script}")

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        report(2, "正在连接 GPU 服务器")
        client = self._connect(config)
        remote_dir = f"{self.remote_project_root}/scripts/gpu/flux_kontext"
        remote_script = f"{remote_dir}/install.sh"
        try:
            self._exec(client, f"mkdir -p {shlex.quote(remote_dir)}", timeout=15)
            sftp = client.open_sftp()
            try:
                sftp.put(str(script), remote_script)
            finally:
                sftp.close()
            self._exec(client, f"chmod +x {shlex.quote(remote_script)}", timeout=15)

            def on_output(line: str) -> None:
                match = re.search(r"\s(\d+)%", line)
                if match:
                    report(
                        6 + int(int(match.group(1)) * 0.87),
                        "正在下载 FLUX.1 Kontext（约 11.9GB）",
                    )
                elif "[KONTEXT_STAGE] models_ready" in line:
                    report(95, "Kontext 模型已完成大小校验")

            report(6, "正在通过 HF 镜像断点续传 FLUX.1 Kontext（约 11.9GB）")
            self._exec_streaming(
                client,
                (f"env HF_ENDPOINT=https://hf-mirror.com {shlex.quote(remote_script)}"),
                timeout=21600,
                output_callback=on_output,
            )
            self._exec(
                client,
                "pkill -f '[p]ython.*main.py.*8188' >/dev/null 2>&1 || true",
                timeout=20,
            )
        finally:
            client.close()

        report(96, "模型已下载，正在启动 ComfyUI 并验证 Kontext 节点")
        status = self.start_comfy(config)
        if not status.kontext_model_ready:
            raise RuntimeError("Kontext 下载完成，但模型或共享 FLUX 依赖检测未通过")
        if not status.kontext_runtime_ready:
            raise RuntimeError("Kontext 模型已安装，但 ComfyUI 编辑节点未就绪")
        report(100, "FLUX.1 Kontext 已安装并可用于动作尾帧")
        return status

    def generate_character(
        self,
        config: GpuConnection,
        *,
        project_slug: str,
        episode_path: Path,
        character: str,
        model_ids: list[str],
        layout_preset: str,
        count: int,
        seed: int,
        local_output_dir: Path,
        prompt: str = "",
        style_prompt: str = "",
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> GenerationResult:
        started_at = time.monotonic()
        selected_models = validate_image_model_ids(model_ids)

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        report(2, "正在连接 GPU 服务器")
        workflow_path = settings.workflows_dir / "krea" / "generate_samples.py"
        if not workflow_path.is_file():
            raise FileNotFoundError(f"Krea 工作流不存在: {workflow_path}")
        if not episode_path.is_file():
            raise FileNotFoundError(f"分镜文件不存在: {episode_path}")
        if not re.fullmatch(r"[A-Za-z0-9_\-\u3400-\u9fff]{1,64}", project_slug):
            raise ValueError("项目名包含不支持的字符")

        client = self._connect(config)
        try:
            self._ensure_remote_comfy(client)
            report(7, "服务器已连接，正在上传任务")
            remote_workflow_dir = f"{self.remote_project_root}/workflows/krea"
            remote_episode_dir = (
                f"{self.remote_project_root}/projects/{project_slug}/production/episodes"
            )
            run_name = time.strftime("%Y%m%d_%H%M%S")
            remote_output_dir = f"{self.remote_project_root}/outputs/image_app/{run_name}"
            self._exec(
                client,
                "mkdir -p "
                + " ".join(
                    shlex.quote(path)
                    for path in (
                        remote_workflow_dir,
                        remote_episode_dir,
                        remote_output_dir,
                    )
                ),
                timeout=10,
            )

            sftp = client.open_sftp()
            remote_workflow = f"{remote_workflow_dir}/generate_samples.py"
            remote_episode = f"{remote_episode_dir}/episode_001.json"
            sftp.put(str(workflow_path), remote_workflow)
            sftp.put(str(episode_path), remote_episode)
            sftp.close()

            command_parts = [
                "/root/miniconda3/bin/python",
                shlex.quote(remote_workflow),
                "--episode",
                shlex.quote(remote_episode),
                "--output-dir",
                shlex.quote(remote_output_dir),
                "--character",
                shlex.quote(character),
                "--portrait-count",
                str(max(1, min(count, 8))),
                "--seed",
                str(seed),
                "--prompt-override",
                shlex.quote(prompt),
                "--style-prompt",
                shlex.quote(style_prompt),
                "--layout-preset",
                shlex.quote(layout_preset),
            ]
            for model_id in selected_models:
                command_parts.extend(("--model", shlex.quote(model_id)))
            command = " ".join(command_parts)
            model_names = "、".join(
                IMAGE_MODEL_PRESETS[model_id].label for model_id in selected_models
            )
            total_images = count * len(selected_models)
            report(10, f"已提交 {model_names}，准备生成 {total_images} 张候选")

            def on_output(line: str) -> None:
                match = re.search(r"\[PROGRESS]\s+(\d+)/(\d+)", line)
                if not match:
                    return
                done, total = int(match.group(1)), int(match.group(2))
                report(
                    10 + int(done / max(total, 1) * 80),
                    f"已生成 {done}/{total} 张候选（{model_names}）",
                )

            remote_log = self._exec_streaming(
                client,
                command,
                timeout=1800,
                output_callback=on_output,
            )

            report(93, "正在下载生成结果")
            local_output_dir.mkdir(parents=True, exist_ok=True)
            sftp = client.open_sftp()
            try:
                names = sorted(sftp.listdir(remote_output_dir))
                for name in names:
                    if name.endswith((".png", ".json")):
                        sftp.get(
                            posixpath.join(remote_output_dir, name),
                            str(local_output_dir / name),
                        )
            finally:
                sftp.close()

            manifest_path = local_output_dir / "manifest.json"
            manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists()
                else {}
            )
            images = sorted(local_output_dir.glob("*.png"))
            if not images:
                raise RuntimeError("远程任务完成，但没有下载到候选图片")
            elapsed = time.monotonic() - started_at
            report(100, f"生成完成，用时 {elapsed:.1f} 秒")
            return GenerationResult(
                local_dir=local_output_dir,
                images=images,
                manifest=manifest,
                remote_log=remote_log,
                elapsed_seconds=elapsed,
            )
        finally:
            client.close()

    def revise_image(
        self,
        config: GpuConnection,
        *,
        source_image: Path,
        local_output_dir: Path,
        prompt: str,
        issue: str,
        negative_prompt: str,
        preservation: str,
        candidate_count: int,
        seed: int,
        width: int,
        height: int,
        context_type: str,
        context_id: str,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> GenerationResult:
        """Revise a generated image with FLUX.1 Kontext and keep its lineage."""

        started_at = time.monotonic()
        source = Path(source_image).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"待修改图片不存在：{source}")
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("待修改图片必须是 PNG、JPG、JPEG 或 WEBP")
        if context_type not in {"character", "shot"}:
            raise ValueError(f"不支持的图片上下文：{context_type}")
        if preservation not in {"strict", "balanced", "creative"}:
            raise ValueError(f"不支持的保留强度：{preservation}")
        if width < 256 or height < 256:
            raise ValueError("修改图片的宽高不能小于 256")
        count = max(1, min(int(candidate_count), 4))

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        workflow_dir = settings.workflows_dir / "krea"
        revision_workflow = workflow_dir / "revise_image.py"
        shared_workflow = workflow_dir / "generate_samples.py"
        for path in (revision_workflow, shared_workflow):
            if not path.is_file():
                raise FileNotFoundError(f"图片修改工作流不存在：{path}")

        report(2, "正在连接 GPU 服务器")
        client = self._connect(config)
        try:
            self._ensure_remote_comfy(client)
            self._ensure_remote_kontext(client)
            run_name = f"revision_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
            remote_workflow_dir = f"{self.remote_project_root}/workflows/krea"
            remote_output_dir = f"{self.remote_project_root}/outputs/image_revisions/{run_name}"
            remote_input_name = f"manju_revision/{run_name}"
            remote_input_dir = f"{self.remote_comfy_root}/input/{remote_input_name}"
            self._exec(
                client,
                "mkdir -p "
                + " ".join(
                    shlex.quote(path)
                    for path in (
                        remote_workflow_dir,
                        remote_output_dir,
                        remote_input_dir,
                    )
                ),
                timeout=15,
            )
            remote_revision = f"{remote_workflow_dir}/revise_image.py"
            remote_shared = f"{remote_workflow_dir}/generate_samples.py"
            remote_source_name = f"source{source.suffix.lower()}"
            remote_source = f"{remote_input_dir}/{remote_source_name}"
            comfy_source = f"{remote_input_name}/{remote_source_name}"
            sftp = client.open_sftp()
            try:
                sftp.put(str(revision_workflow), remote_revision)
                sftp.put(str(shared_workflow), remote_shared)
                sftp.put(str(source), remote_source)
            finally:
                sftp.close()

            command = " ".join(
                (
                    "/root/miniconda3/bin/python",
                    shlex.quote(remote_revision),
                    "--source-image",
                    shlex.quote(comfy_source),
                    "--output-dir",
                    shlex.quote(remote_output_dir),
                    "--prompt",
                    shlex.quote(prompt),
                    "--issue",
                    shlex.quote(issue),
                    "--negative-prompt",
                    shlex.quote(negative_prompt),
                    "--preservation",
                    shlex.quote(preservation),
                    "--candidate-count",
                    str(count),
                    "--seed",
                    str(max(1, int(seed))),
                    "--width",
                    str(int(width)),
                    "--height",
                    str(int(height)),
                    "--context-type",
                    shlex.quote(context_type),
                    "--context-id",
                    shlex.quote(context_id),
                )
            )
            report(8, f"正在用 FLUX.1 Kontext 生成 {count} 个修改候选")

            def on_output(line: str) -> None:
                match = re.search(r"\[PROGRESS]\s+(\d+)/(\d+)", line)
                if not match:
                    return
                done, total = int(match.group(1)), int(match.group(2))
                report(
                    8 + int(done / max(total, 1) * 82),
                    f"已完成修改候选 {done}/{total}",
                )

            remote_log = self._exec_streaming(
                client,
                command,
                timeout=1800 * count,
                output_callback=on_output,
            )
            report(93, "正在下载修改候选与版本记录")
            local_output_dir.mkdir(parents=True, exist_ok=True)
            sftp = client.open_sftp()
            try:
                for name in sorted(sftp.listdir(remote_output_dir)):
                    if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".json")):
                        sftp.get(
                            posixpath.join(remote_output_dir, name),
                            str(local_output_dir / name),
                        )
            finally:
                sftp.close()

            manifest_path = local_output_dir / "manifest.json"
            manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.is_file()
                else {}
            )
            manifest["source_image"] = str(source)
            for record in manifest.get("images") or []:
                if isinstance(record, dict):
                    record["source_image"] = str(source)
            atomic_write_json(manifest_path, manifest)
            images = sorted(
                path
                for path in local_output_dir.iterdir()
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            )
            if not images:
                raise RuntimeError("图片修改任务完成，但没有下载到候选图片")
            elapsed = time.monotonic() - started_at
            report(100, f"图片修改完成，用时 {elapsed:.1f} 秒")
            return GenerationResult(
                local_dir=local_output_dir,
                images=images,
                manifest=manifest,
                remote_log=remote_log,
                elapsed_seconds=elapsed,
            )
        finally:
            client.close()

    def generate_shot_images(
        self,
        config: GpuConnection,
        *,
        project_slug: str,
        episode_path: Path,
        shot_numbers: list[int],
        model_ids: list[str],
        local_output_dir: Path,
        candidate_count: int = 1,
        seed: int = 20260728,
        width: int = 832,
        height: int = 480,
        style_prompt: str = "",
        frame_role: str = "start",
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> GenerationResult:
        """Generate missing storyboard keyframes and download their manifests."""

        started_at = time.monotonic()
        selected_models = validate_image_model_ids(model_ids)
        selected_shots = sorted({int(number) for number in shot_numbers if int(number) > 0})
        if not selected_shots:
            raise ValueError("没有需要补全画面的镜头")
        if frame_role not in {"start", "end"}:
            raise ValueError(f"不支持的关键帧类型：{frame_role}")
        if width % 16 or height % 16:
            raise ValueError("分镜画面宽高必须是 16 的倍数")
        if not episode_path.is_file():
            raise FileNotFoundError(f"分镜文件不存在: {episode_path}")
        if not re.fullmatch(r"[A-Za-z0-9_\-\u3400-\u9fff]{1,64}", project_slug):
            raise ValueError("项目名包含不支持的字符")

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        workflow_dir = settings.workflows_dir / "krea"
        shot_workflow = workflow_dir / "generate_shots.py"
        shared_workflow = workflow_dir / "generate_samples.py"
        for path in (shot_workflow, shared_workflow):
            if not path.is_file():
                raise FileNotFoundError(f"分镜生图工作流不存在: {path}")

        report(2, "正在连接 GPU 服务器")
        client = self._connect(config)
        try:
            self._ensure_remote_comfy(client)
            episode_value = json.loads(episode_path.read_text(encoding="utf-8-sig"))
            episode_number = int(episode_value.get("episode_number") or 1)
            run_name = (
                f"episode_{episode_number:03d}_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
            )
            remote_workflow_dir = f"{self.remote_project_root}/workflows/krea"
            remote_episode_dir = (
                f"{self.remote_project_root}/projects/{project_slug}/production/episodes"
            )
            remote_output_dir = f"{self.remote_project_root}/outputs/shot_image_app/{run_name}"
            remote_reference_name = f"manju_continuity/{run_name}"
            remote_reference_dir = f"{self.remote_comfy_root}/input/{remote_reference_name}"
            self._exec(
                client,
                "mkdir -p "
                + " ".join(
                    shlex.quote(path)
                    for path in (
                        remote_workflow_dir,
                        remote_episode_dir,
                        remote_output_dir,
                        remote_reference_dir,
                    )
                ),
                timeout=15,
            )
            remote_shot_workflow = f"{remote_workflow_dir}/generate_shots.py"
            remote_shared_workflow = f"{remote_workflow_dir}/generate_samples.py"
            remote_episode = f"{remote_episode_dir}/episode_{episode_number:03d}.json"
            shot_items = {
                int(item.get("shot_number") or index): item
                for index, item in enumerate(
                    episode_value.get("shots") or [],
                    start=1,
                )
                if isinstance(item, dict)
            }
            selected_set = set(selected_shots)
            project_root = episode_path.resolve().parents[2]
            cast_selection_value = self._read_local_json(
                project_root / "production" / "cast_selection.json"
            )
            cast_selections = cast_selection_value.get("selections") or {}
            cast_selections = (
                {
                    str(name): str(path)
                    for name, path in cast_selections.items()
                    if str(name).strip() and str(path).strip()
                }
                if isinstance(cast_selections, dict)
                else {}
            )
            # Identity anchors are for UI selection; generation uses approved
            # three-view turnarounds whenever the cast record provides one.
            cast_references = cast_selection_value.get("references") or {}
            if isinstance(cast_references, dict):
                for name, value in cast_references.items():
                    if not isinstance(value, dict):
                        continue
                    angles = value.get("angles") or {}
                    front = str(angles.get("front") or "").strip() if isinstance(angles, dict) else ""
                    turnaround = str(value.get("turnaround") or "").strip()
                    # Use a single approved front view as the image condition;
                    # retain the turnaround path in continuity metadata.
                    if front:
                        cast_selections[str(name)] = front
                    elif turnaround:
                        cast_selections[str(name)] = turnaround

            def local_shot_image(shot: dict[str, Any]) -> Path | None:
                video = shot.get("video_generation")
                video = video if isinstance(video, dict) else {}
                generation = shot.get("image_generation")
                generation = generation if isinstance(generation, dict) else {}
                for configured in (
                    video.get("source_image"),
                    generation.get("selected_image"),
                    generation.get("selected_source"),
                ):
                    if not configured:
                        continue
                    candidate = (project_root / str(configured)).resolve()
                    if candidate.is_relative_to(project_root) and candidate.is_file():
                        return candidate
                return None

            sftp = client.open_sftp()
            try:
                sftp.put(str(shot_workflow), remote_shot_workflow)
                sftp.put(str(shared_workflow), remote_shared_workflow)
                for shot_number in selected_shots:
                    shot = shot_items.get(shot_number)
                    if not shot:
                        continue
                    continuity = shot.get("continuity_plan")
                    continuity = dict(continuity) if isinstance(continuity, dict) else {}
                    if frame_role == "end":
                        start_path = local_shot_image(shot)
                        if not start_path:
                            raise FileNotFoundError(
                                f"镜头 {shot_number:02d} 缺少起始帧，不能生成结束帧"
                            )
                        reference_filename = (
                            f"end_from_start_{shot_number:03d}{start_path.suffix.lower()}"
                        )
                        remote_reference = f"{remote_reference_dir}/{reference_filename}"
                        sftp.put(str(start_path), remote_reference)
                        continuity["reference_mode"] = "previous_in_group"
                        continuity["reference_shot_number"] = shot_number
                        continuity["reference_image"] = (
                            f"{remote_reference_name}/{reference_filename}"
                        )
                        continuity["reference_denoise"] = 0.68
                        shot["continuity_plan"] = continuity
                        continue
                    cast_reference = _resolve_cast_reference(
                        project_root,
                        cast_selections,
                        shot,
                    )
                    if cast_reference:
                        cast_name, reference_path = cast_reference
                        reference_filename = (
                            f"cast_{shot_number:03d}{reference_path.suffix.lower()}"
                        )
                        remote_reference = f"{remote_reference_dir}/{reference_filename}"
                        sftp.put(str(reference_path), remote_reference)
                        continuity["reference_mode"] = "cast_selection"
                        continuity["reference_shot_number"] = 0
                        continuity["reference_image"] = (
                            f"{remote_reference_name}/{reference_filename}"
                        )
                        continuity["reference_denoise"] = CAST_REFERENCE_DENOISE
                        continuity["cast_reference_character"] = cast_name
                        shot["continuity_plan"] = continuity
                        continue
                    reference_number = int(continuity.get("reference_shot_number") or 0)
                    if not reference_number or reference_number in selected_set:
                        continue
                    reference_shot = shot_items.get(reference_number)
                    reference_path = local_shot_image(reference_shot) if reference_shot else None
                    if not reference_path:
                        continue
                    reference_filename = (
                        f"shot_{reference_number:03d}{reference_path.suffix.lower()}"
                    )
                    remote_reference = f"{remote_reference_dir}/{reference_filename}"
                    sftp.put(str(reference_path), remote_reference)
                    continuity["reference_image"] = f"{remote_reference_name}/{reference_filename}"
                    shot["continuity_plan"] = continuity
                with sftp.file(remote_episode, "wb") as handle:
                    handle.write(
                        json.dumps(
                            episode_value,
                            ensure_ascii=False,
                            indent=2,
                        ).encode("utf-8")
                    )
            finally:
                sftp.close()

            command_parts = [
                "/root/miniconda3/bin/python",
                shlex.quote(remote_shot_workflow),
                "--episode",
                shlex.quote(remote_episode),
                "--output-dir",
                shlex.quote(remote_output_dir),
                "--candidate-count",
                str(max(1, min(candidate_count, 4))),
                "--seed",
                str(seed),
                "--width",
                str(width),
                "--height",
                str(height),
                "--style-prompt",
                shlex.quote(style_prompt),
                "--frame-role",
                shlex.quote(frame_role),
                "--end-frame-editor",
                "kontext" if frame_role == "end" else "legacy",
            ]
            for shot_number in selected_shots:
                command_parts.extend(("--shot-number", str(shot_number)))
            for model_id in selected_models:
                command_parts.extend(("--model", shlex.quote(model_id)))
            command = " ".join(command_parts)
            total_images = (
                len(selected_shots)
                * (1 if frame_role == "end" else len(selected_models))
                * max(1, min(candidate_count, 4))
            )
            frame_label = "结束关键帧" if frame_role == "end" else "分镜首帧"
            report(8, f"准备生成 {total_images} 张{frame_label}")

            def on_output(line: str) -> None:
                match = re.search(r"\[PROGRESS]\s+(\d+)/(\d+)", line)
                if not match:
                    return
                done, total = int(match.group(1)), int(match.group(2))
                report(
                    8 + int(done / max(total, 1) * 82),
                    f"已生成 {done}/{total} 张{frame_label}",
                )

            remote_log = self._exec_streaming(
                client,
                command,
                timeout=1800 * total_images,
                output_callback=on_output,
            )
            report(93, f"正在下载{frame_label}并准备自动回填")
            local_output_dir.mkdir(parents=True, exist_ok=True)
            sftp = client.open_sftp()
            try:
                for name in sorted(sftp.listdir(remote_output_dir)):
                    if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".json")):
                        sftp.get(
                            posixpath.join(remote_output_dir, name),
                            str(local_output_dir / name),
                        )
            finally:
                sftp.close()

            manifest_path = local_output_dir / "manifest.json"
            manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.is_file()
                else {}
            )
            images = sorted(
                path
                for path in local_output_dir.iterdir()
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            )
            if not images:
                raise RuntimeError("远程任务完成，但没有下载到分镜首帧")
            elapsed = time.monotonic() - started_at
            report(100, f"分镜首帧生成完成，用时 {elapsed:.1f} 秒")
            return GenerationResult(
                local_dir=local_output_dir,
                images=images,
                manifest=manifest,
                remote_log=remote_log,
                elapsed_seconds=elapsed,
            )
        finally:
            client.close()

    @staticmethod
    def _read_local_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def generate_h3_videos(
        self,
        config: GpuConnection,
        project_root: Path,
        specs: list[VideoRenderSpec],
        *,
        progress_callback: Callable[[int, str], None] | None = None,
        clip_callback: Callable[[VideoClipResult], None] | None = None,
    ) -> VideoBatchResult:
        """Run MiniMax H3 FL2VA with native stereo audio on remote ComfyUI."""

        if not specs:
            raise ValueError("请至少选择一个镜头")
        unsupported = {
            spec.engine_profile for spec in specs if spec.engine_profile != "minimax_h3_fl2va"
        }
        if unsupported:
            raise ValueError(
                f"MiniMax H3 适配器只接受 minimax_h3_fl2va；收到：{'、'.join(sorted(unsupported))}"
            )
        for spec in specs:
            if spec.width % 32 or spec.height % 32:
                raise ValueError("MiniMax H3 视频宽高必须是 32 的倍数")
            if spec.fps != 24:
                raise ValueError("MiniMax H3 固定使用 24fps")
            if spec.native_audio_mode == "native_full" and not spec.dialogue_prompt.strip():
                raise ValueError("MiniMax H3 原生完整声音模式必须提供逐字对白提示")

        root = Path(project_root).resolve()
        init_db(root / "database" / "world.db")
        payload = [spec.model_dump(mode="json") for spec in specs]
        job = create_job(
            "video_generate_minimax_h3",
            payload={"specs": payload},
            input_hash=sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            reuse_existing=False,
        )
        transition_job(job.id, JobStatus.RUNNING)
        started = time.monotonic()
        results: list[VideoClipResult] = []

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        client: paramiko.SSHClient | None = None
        report(2, "正在连接 GPU 服务器")
        try:
            workflow_path = settings.workflows_dir / "minimax_h3" / "generate_video.py"
            if not workflow_path.is_file():
                raise FileNotFoundError(f"MiniMax H3 工作流不存在：{workflow_path}")
            client = self._connect(config)
            self._ensure_remote_comfy(client)
            self._ensure_remote_h3(client)
            self._ensure_remote_t8(client)
            report(5, "MiniMax H3 FL2VA 与 T8 音视频节点已就绪")

            run_name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
            remote_workflow_dir = f"{self.remote_project_root}/workflows/minimax_h3"
            remote_input_dir = f"{self.remote_project_root}/inputs/h3_video_app/{run_name}"
            remote_output_root = f"{self.remote_project_root}/outputs/h3_video_app/{run_name}"
            self._exec(
                client,
                "mkdir -p "
                + " ".join(
                    shlex.quote(path)
                    for path in (
                        remote_workflow_dir,
                        remote_input_dir,
                        remote_output_root,
                    )
                ),
                timeout=15,
            )
            remote_workflow = f"{remote_workflow_dir}/generate_video.py"
            sftp = client.open_sftp()
            try:
                sftp.put(str(workflow_path), remote_workflow)
            finally:
                sftp.close()

            total_candidates = sum(spec.candidate_count for spec in specs)
            completed_candidates = 0
            for shot_index, spec in enumerate(specs, start=1):
                source = self._project_file(root, spec.source_image, "起始帧")
                end_source = (
                    self._project_file(root, spec.end_image, "结束帧") if spec.end_image else None
                )
                remote_source = (
                    f"{remote_input_dir}/shot_{spec.shot_number:03d}_start{source.suffix.lower()}"
                )
                remote_end = (
                    f"{remote_input_dir}/shot_{spec.shot_number:03d}_end{end_source.suffix.lower()}"
                    if end_source
                    else ""
                )
                remote_output_dir = f"{remote_output_root}/shot_{spec.shot_number:03d}"
                remote_prompt = f"{remote_input_dir}/shot_{spec.shot_number:03d}_prompt.txt"
                remote_reference_audio = ""
                reference_audio_source: Path | None = None
                if spec.reference_audio:
                    reference_audio_source = self._project_file(
                        root, spec.reference_audio, "参考音频"
                    )
                    remote_reference_audio = (
                        f"{remote_input_dir}/shot_{spec.shot_number:03d}_ref"
                        f"{reference_audio_source.suffix.lower()}"
                    )
                self._exec(
                    client,
                    f"mkdir -p {shlex.quote(remote_output_dir)}",
                    timeout=10,
                )
                sftp = client.open_sftp()
                try:
                    sftp.put(str(source), remote_source)
                    if end_source:
                        sftp.put(str(end_source), remote_end)
                    if reference_audio_source:
                        sftp.put(str(reference_audio_source), remote_reference_audio)
                    with sftp.file(remote_prompt, "wb") as prompt_file:
                        prompt_file.write(self._h3_positive_prompt(spec).encode("utf-8"))
                finally:
                    sftp.close()

                frame_count = round(spec.duration_seconds * 24)
                seed = int(time.time_ns() % 2_147_000_000) + spec.shot_number * 100
                command_parts = [
                    "/root/miniconda3/bin/python",
                    shlex.quote(remote_workflow),
                    "--source-image",
                    shlex.quote(remote_source),
                ]
                if remote_end:
                    command_parts.extend(["--end-image", shlex.quote(remote_end)])
                if remote_reference_audio:
                    command_parts.extend(
                        ["--reference-audio", shlex.quote(remote_reference_audio)]
                    )
                command_parts.extend(
                    [
                        "--output-dir",
                        shlex.quote(remote_output_dir),
                        "--run-name",
                        shlex.quote(f"{run_name}/shot_{spec.shot_number:03d}"),
                        "--positive-prompt-file",
                        shlex.quote(remote_prompt),
                        "--width",
                        str(spec.width),
                        "--height",
                        str(spec.height),
                        "--frame-count",
                        str(frame_count),
                        "--fps",
                        "24",
                        "--seed",
                        str(seed),
                        "--candidate-count",
                        str(spec.candidate_count),
                        "--engine",
                        H3_WORKFLOW_ENGINE,
                        "--steps",
                        str(spec.steps or 20),
                        "--sampler-name",
                        H3_T8_SAMPLER_NAME,
                        "--scheduler",
                        H3_T8_SCHEDULER,
                        "--shift-video",
                        str(H3_T8_SHIFT_VIDEO),
                        "--shift-audio",
                        str(H3_T8_SHIFT_AUDIO),
                        "--audio-mode",
                        GpuServerService._h3_t8_audio_mode(spec),
                    ]
                )

                def on_output(
                    line: str,
                    shot_number: int = spec.shot_number,
                ) -> None:
                    nonlocal completed_candidates
                    match = re.search(
                        r"\[PROGRESS]\s+(\d+)/(\d+)\s+complete",
                        line,
                    )
                    if match:
                        completed_candidates += 1
                        ratio = completed_candidates / max(total_candidates, 1)
                        report(
                            7 + int(ratio * 84),
                            f"H3 镜头 {shot_number:02d}："
                            f"已完成候选 {match.group(1)}/{match.group(2)}",
                        )

                report(
                    7 + int((shot_index - 1) / len(specs) * 84),
                    f"正在生成 H3 镜头 {spec.shot_number:02d}"
                    f"（{shot_index}/{len(specs)}，含原生声音）",
                )
                self._exec_streaming(
                    client,
                    " ".join(command_parts),
                    timeout=15000 * spec.candidate_count,
                    output_callback=on_output,
                )

                local_output_dir = (
                    root
                    / "production"
                    / "videos"
                    / f"episode_{spec.episode_number:03d}"
                    / f"shot_{spec.shot_number:03d}"
                )
                local_output_dir.mkdir(parents=True, exist_ok=True)
                sftp = client.open_sftp()
                try:
                    remote_names = sorted(sftp.listdir(remote_output_dir))
                    local_remote_manifest = local_output_dir / f".h3_manifest_{run_name}.json"
                    sftp.get(
                        f"{remote_output_dir}/manifest.json",
                        str(local_remote_manifest),
                    )
                    remote_manifest = json.loads(local_remote_manifest.read_text(encoding="utf-8"))
                    local_remote_manifest.unlink(missing_ok=True)
                    output_records = {
                        str(item.get("file")): item
                        for item in remote_manifest.get("outputs") or []
                        if isinstance(item, dict)
                    }
                    shot_results: list[VideoClipResult] = []
                    for remote_name in remote_names:
                        if not remote_name.lower().endswith((".mp4", ".webm", ".mkv", ".mov")):
                            continue
                        record = output_records.get(remote_name) or {}
                        candidate_index = int(
                            record.get("candidate_index") or len(shot_results) + 1
                        )
                        suffix = Path(remote_name).suffix.lower()
                        destination = local_output_dir / (
                            f"shot_{spec.shot_number:03d}_{run_name}_"
                            f"h3_c{candidate_index:02d}{suffix}"
                        )
                        sftp.get(
                            f"{remote_output_dir}/{remote_name}",
                            str(destination),
                        )
                        native_audio_target_lufs = (
                            -18.0 if spec.native_audio_mode == "native_full" else -24.0
                        )
                        native_audio_normalized = VideoRenderService().normalize_native_audio(
                            destination,
                            target_lufs=native_audio_target_lufs,
                        )
                        qc_sheet_name = str(record.get("qc_contact_sheet") or "")
                        qc_sheet_path: Path | None = None
                        if qc_sheet_name and qc_sheet_name in remote_names:
                            qc_sheet_path = destination.with_name(f"{destination.stem}_qc.jpg")
                            sftp.get(
                                f"{remote_output_dir}/{qc_sheet_name}",
                                str(qc_sheet_path),
                            )
                        elapsed = float(
                            remote_manifest.get("elapsed_seconds") or time.monotonic() - started
                        )
                        manifest_path = destination.with_name(
                            f"manifest_{run_name}_h3_c{candidate_index:02d}.json"
                        )
                        metadata = VideoArtifactMetadata(
                            engine_profile=spec.engine_profile,
                            episode_number=spec.episode_number,
                            shot_number=spec.shot_number,
                            source_image=source.relative_to(root).as_posix(),
                            end_image=(
                                end_source.relative_to(root).as_posix() if end_source else ""
                            ),
                            output_file=destination.relative_to(root).as_posix(),
                            subject_motion=spec.subject_motion,
                            environment_motion=spec.environment_motion,
                            continuity_constraints=spec.continuity_constraints,
                            negative_prompt=spec.negative_prompt,
                            motion_prompt=self._h3_positive_prompt(spec),
                            native_audio_mode=spec.native_audio_mode,
                            dialogue_prompt=spec.dialogue_prompt,
                            sound_effect_prompt=spec.sound_effect_prompt,
                            music_prompt=spec.music_prompt,
                            native_audio=True,
                            camera_movement=spec.camera_movement,
                            motion_strength=spec.motion_strength,
                            screen_direction=spec.screen_direction,
                            transition_out=spec.transition_out,
                            transition_frames=spec.transition_frames,
                            handle_frames=spec.handle_frames,
                            candidate_count=spec.candidate_count,
                            technical_qc=dict(record.get("technical_qc") or {}),
                            approval_status=str(
                                record.get("approval_status") or "rejected_technical"
                            ),
                            duration_seconds=spec.duration_seconds,
                            fps=24,
                            width=spec.width,
                            height=spec.height,
                            generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                            elapsed_seconds=elapsed,
                            job_id=job.id,
                        ).model_dump(mode="json")
                        metadata.update(
                            {
                                "model_name": remote_manifest.get("model"),
                                "text_encoder": remote_manifest.get("text_encoder"),
                                "generation_revision": H3_GENERATION_REVISION,
                                "workflow_engine": remote_manifest.get("engine"),
                                "audio_mode": remote_manifest.get("audio_mode"),
                                "reference_audio": remote_manifest.get("reference_audio"),
                                "steps": remote_manifest.get("steps"),
                                "sampler_name": remote_manifest.get("sampler_name"),
                                "scheduler": remote_manifest.get("scheduler"),
                                "shift_video": remote_manifest.get("shift_video"),
                                "shift_audio": remote_manifest.get("shift_audio"),
                                "video_vae": remote_manifest.get("video_vae"),
                                "audio_vae": remote_manifest.get("audio_vae"),
                                "candidate_index": candidate_index,
                                "seed": record.get("seed"),
                                "remote_prompt_id": record.get("prompt_id"),
                                "native_audio_normalized": native_audio_normalized,
                                "native_audio_target_lufs": native_audio_target_lufs,
                                "qc_contact_sheet": (
                                    qc_sheet_path.relative_to(root).as_posix()
                                    if qc_sheet_path
                                    else ""
                                ),
                                "remote_manifest": remote_manifest,
                            }
                        )
                        atomic_write_json(manifest_path, metadata)
                        register_artifact(
                            root,
                            destination,
                            kind="shot_video",
                            job_id=job.id,
                            metadata=metadata,
                        )
                        shot_results.append(
                            VideoClipResult(
                                episode_number=spec.episode_number,
                                shot_number=spec.shot_number,
                                video_path=destination,
                                manifest_path=manifest_path,
                                source_image=source,
                                elapsed_seconds=elapsed,
                                candidate_index=candidate_index,
                            )
                        )
                        if clip_callback is not None:
                            clip_callback(shot_results[-1])
                    results.extend(shot_results)
                finally:
                    sftp.close()
                if not shot_results:
                    raise RuntimeError(f"H3 镜头 {spec.shot_number:02d} 完成但没有视频输出")
                heartbeat_job(job.id, shot_index / len(specs) * 0.95)

            elapsed = time.monotonic() - started
            transition_job(
                job.id,
                JobStatus.SUCCEEDED,
                result={
                    "clips": [item.video_path.relative_to(root).as_posix() for item in results],
                    "elapsed_seconds": elapsed,
                },
            )
            report(100, f"MiniMax H3 已生成 {len(results)} 个音视频候选")
            return VideoBatchResult(
                clips=results,
                job_id=job.id,
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            transition_job(
                job.id,
                JobStatus.FAILED,
                error_code="minimax_h3_video_render_failed",
                error_message=str(exc),
            )
            raise
        finally:
            if client is not None:
                client.close()

    def _ensure_remote_h3(self, client: paramiko.SSHClient) -> None:
        command = r"""
models=/root/autodl-tmp/ComfyUI/models
test -f "$models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"
test -f "$models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
test -f "$models/vae/minimax_h3_video_vae_fp16.safetensors"
test -f "$models/vae/minimax_h3_audio_vae_fp32.safetensors"
object_info=$(curl -fsS --max-time 10 http://127.0.0.1:8188/object_info)
printf '%s' "$object_info" | grep -q '"MiniMaxH3ImageToVideo"'
printf '%s' "$object_info" | grep -q '"VAEDecodeAudio"'
printf '%s' "$object_info" | grep -q '"CreateVideo"'
printf '%s' "$object_info" | grep -q '"SaveVideo"'
"""
        try:
            self._exec(client, command, timeout=30)
        except Exception as exc:
            raise RuntimeError(
                "MiniMax H3 FL2VA 模型或 ComfyUI 原生音视频节点不完整；"
                "请先更新 ComfyUI，并安装 H3 模型、文本编码器和两个 VAE。"
            ) from exc

    def _ensure_remote_t8(self, client: paramiko.SSHClient) -> None:
        command = r"""
object_info=$(curl -fsS --max-time 10 http://127.0.0.1:8188/object_info)
printf '%s' "$object_info" | grep -q '"MiniMaxH3AudioConditioningT8"'
printf '%s' "$object_info" | grep -q '"MiniMaxH3DualClockSamplerT8"'
printf '%s' "$object_info" | grep -q '"MiniMaxH3AVDecodeT8"'
printf '%s' "$object_info" | grep -q '"MiniMaxH3AudioMixT8"'
printf '%s' "$object_info" | grep -q '"MiniMaxH3OutputTrimT8"'
"""
        try:
            self._exec(client, command, timeout=30)
        except Exception as exc:
            raise RuntimeError(
                "MiniMax H3 Audio T8 音视频增强节点不完整；"
                "请先在“连接与设置”安装/修复 T8 音频增强包。"
            ) from exc

    def _ensure_remote_kontext(self, client: paramiko.SSHClient) -> None:
        command = r"""
models=/root/autodl-tmp/ComfyUI/models
test -f "$models/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"
test -f "$models/text_encoders/clip_l.safetensors"
test -f "$models/text_encoders/t5xxl_fp8_e4m3fn.safetensors"
test -f "$models/vae/ae.safetensors"
object_info=$(curl -fsS --max-time 10 http://127.0.0.1:8188/object_info)
printf '%s' "$object_info" | grep -q '"FluxKontextImageScale"'
printf '%s' "$object_info" | grep -q '"ReferenceLatent"'
printf '%s' "$object_info" | grep -q '"ConditioningZeroOut"'
"""
        try:
            self._exec(client, command, timeout=30)
        except Exception as exc:
            raise RuntimeError(
                "FLUX.1 Kontext 模型或 ComfyUI 编辑节点不完整；请先在连接设置中安装/修复 Kontext。"
            ) from exc

    @staticmethod
    def _h3_t8_audio_mode(spec: VideoRenderSpec) -> str:
        """Map the app-level audio mode to the T8 conditioning audio_mode.

        Every app mode (off / ambience_sfx_music / native_full) synthesizes the
        audio track from the prompt inside T8's ``native`` mode; the differences
        live in the prompt text already produced by :meth:`_h3_positive_prompt`.
        A non-empty :attr:`VideoRenderSpec.audio_mode_override` passes straight
        through so power users can select lock_source / remix_source /
        reference_only directly.
        """
        override = (spec.audio_mode_override or "").strip()
        return override if override else "native"

    @staticmethod
    def _h3_positive_prompt(spec: VideoRenderSpec) -> str:
        duration = max(4.0, min(spec.duration_seconds, 15.0))
        handle_seconds = max(3, min(spec.handle_frames, 12)) / 24
        setup_end = max(handle_seconds, duration * 0.18)
        action_end = max(setup_end + 0.8, duration - handle_seconds)
        transition_instruction = {
            "match_cut": (
                "End on a clear silhouette and directional action vector matching the "
                "next shot; preserve momentum without completing a second action."
            ),
            "dissolve": (
                "Keep the final face, body pose, lighting and background geometry "
                "stable for the dissolve handle."
            ),
            "fade_black": ("Resolve the action and hold a clean stable composition for the fade."),
            "cut": (
                "End on a clear readable action punctuation with the face unobscured "
                "and no late camera jerk."
            ),
        }.get(spec.transition_out, "")
        audio_mode = spec.native_audio_mode
        if audio_mode == "native_full":
            spoken_line = GpuServerService._spoken_line(spec.dialogue_prompt)
            speaker_match = re.search(r"说话人：([^。]+)", spec.dialogue_prompt)
            placement_match = re.search(r"发声位置：([^。]+)", spec.dialogue_prompt)
            speaker = speaker_match.group(1).strip() if speaker_match else "指定说话人"
            placement = placement_match.group(1).strip() if placement_match else "按提示词指定"
            dialogue = (
                f"Audio direction: speaker={speaker}; placement={placement}. "
                f"Speak only this literal line once: {spoken_line}"
                if spoken_line
                else "Dialogue: no spoken line in this shot."
            )
        elif audio_mode == "off":
            dialogue = "Audio: complete silence; no dialogue, music, or effects."
        else:
            dialogue = (
                "Dialogue: no spoken dialogue and no narration; leave the vocal "
                "frequency range clear for precise post-production dubbing."
            )
        sound = (
            spec.sound_effect_prompt.strip()
            or "Clearly audible natural ambience, synchronized footsteps and cloth movement."
        )
        music = (
            spec.music_prompt.strip()
            or "Audible but restrained cinematic Chinese xianxia underscore, no vocals."
        )
        if audio_mode == "off":
            sound_block = ""
        else:
            sound_block = f"Sound effects: {sound}\nMusic: {music}"

        negative_terms = [
            term.strip()
            for term in spec.negative_prompt.split(",")
            if term.strip().lower()
            not in {
                "fast motion",
                "sudden motion",
                "rapid head turn",
                "talking",
                "lip sync",
                "open mouth",
            }
        ]
        restrictions = ", ".join(negative_terms[:24])
        end_frame = (
            "The motion resolves exactly into the supplied last-frame pose and composition."
            if spec.end_image
            else "The motion ends on a stable, readable pose that can cut cleanly."
        )
        if spec.chained_from_previous:
            opening = (
                "Continue seamlessly from the previous shot's final frame; "
                "preserve the inherited pose, screen direction, costume and "
                "momentum into the first boundary, then let natural breathing "
                f"and weight shift carry the action forward for approximately "
                f"{handle_seconds:.2f}s."
            )
        else:
            opening = (
                f"Begin exactly from the supplied first frame. Preserve identity, "
                "costume, props, screen direction, eyelines and background "
                f"geometry. Keep the first boundary readable for approximately "
                f"{handle_seconds:.2f}s while natural breathing and weight shift "
                "initiate the action."
            )
        return "\n\n".join(
            part
            for part in (
                (
                    "Realistic live-action Chinese xianxia cinematic shot. "
                    f"Scene overview: {spec.scene_description.strip()}"
                ),
                (
                    "Timeline:\n"
                    f"[0.00s-{setup_end:.2f}s] {opening}\n"
                    f"[{setup_end:.2f}s-{action_end:.2f}s] Perform one physically "
                    f"coherent action: {spec.subject_motion.strip() or spec.motion_prompt.strip()}. "
                    f"Environment motion: {spec.environment_motion.strip()}.\n"
                    f"[{action_end:.2f}s-{duration:.2f}s] Complete the action with "
                    f"natural momentum and settle. Hold the final boundary for "
                    f"approximately {handle_seconds:.2f}s. {end_frame} "
                    f"{transition_instruction}"
                ),
                (
                    f"Camera: {spec.camera_movement}; motion strength "
                    f"{spec.motion_strength}; one continuous shot, stable horizon, "
                    "no unexplained cuts or teleporting."
                ),
                (
                    f"Continuity: {spec.continuity_constraints.strip()} "
                    "Hands, feet and held props follow plausible anatomy and contact; "
                    "no identity swaps or newly invented foreground people."
                ),
                dialogue,
                sound_block,
                f"Avoid: {restrictions}." if restrictions else "",
            )
            if part
        )

    @staticmethod
    def _spoken_line(value: str) -> str:
        """Extract only the literal utterance from a control prompt."""
        text = str(value or "").strip()
        if not text:
            return ""
        match = re.search(r"『([^』]+)』", text)
        if match:
            return match.group(1).strip()
        if "：" in text:
            return text.rsplit("：", 1)[-1].strip().rstrip("。")
        return text

    @staticmethod
    def _project_file(root: Path, path: Path, label: str) -> Path:
        candidate = Path(path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{label}必须位于当前项目目录内") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"{label}不存在：{candidate}")
        return candidate

    def _probe_remote_comfy(self, client: paramiko.SSHClient) -> bool:
        probe = (
            "curl -fsS --max-time 3 http://127.0.0.1:8188/system_stats "
            ">/dev/null 2>&1 && echo ONLINE || true"
        )
        return "ONLINE" in self._exec(client, probe, timeout=8)

    def _stop_remote_cosyvoice(self, client: paramiko.SSHClient) -> bool:
        output = self._exec(
            client,
            (
                "pid_file=/root/cosyvoice-service/cosyvoice.pid; "
                'pid=$(cat "$pid_file" 2>/dev/null || true); '
                'case "$pid" in (*[!0-9]*|"") echo NOT_RUNNING; exit 0;; esac; '
                'if ! kill -0 "$pid" 2>/dev/null; then echo NOT_RUNNING; exit 0; fi; '
                'kill -TERM "$pid" 2>/dev/null || true; '
                "for _ in $(seq 1 30); do "
                'kill -0 "$pid" 2>/dev/null || { echo STOPPED; exit 0; }; '
                "sleep 0.5; done; "
                'kill -KILL "$pid" 2>/dev/null || true; echo STOPPED'
            ),
            timeout=25,
        )
        return "STOPPED" in output

    def _stop_remote_comfy(
        self,
        client: paramiko.SSHClient,
        *,
        require_idle: bool = True,
    ) -> bool:
        if not self._probe_remote_comfy(client):
            return False
        if require_idle:
            queue_raw = self._exec(
                client,
                "curl -fsS --max-time 5 http://127.0.0.1:8188/queue",
                timeout=10,
            )
            try:
                queue = json.loads(queue_raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("无法确认 ComfyUI 队列状态，拒绝切换服务") from exc
            if queue.get("queue_running") or queue.get("queue_pending"):
                raise RuntimeError("ComfyUI 仍有生成任务，不能切换到 CosyVoice")
        self._exec(
            client,
            (
                "pids=$(pgrep -f 'main\\.py .*--port 8188' 2>/dev/null || true); "
                '[ -z "$pids" ] && exit 0; '
                "kill -TERM $pids 2>/dev/null || true; "
                "for _ in $(seq 1 30); do "
                "remaining=''; for pid in $pids; do "
                'kill -0 "$pid" 2>/dev/null && remaining="$remaining $pid"; '
                'done; [ -z "$remaining" ] && exit 0; sleep 0.5; done; '
                "kill -KILL $remaining 2>/dev/null || true"
            ),
            timeout=25,
        )
        return True

    def _ensure_remote_comfy(self, client: paramiko.SSHClient) -> None:
        # Image/video generation owns the GPU. Keep the switch idempotent so
        # direct service calls are as safe as the desktop workflow.
        self._stop_remote_cosyvoice(client)
        if self._probe_remote_comfy(client):
            return

        process_probe = (
            "pgrep -f 'main\\.py .*--port 8188' >/dev/null 2>&1 && echo STARTING || true"
        )
        if "STARTING" in self._exec(client, process_probe, timeout=8):
            for _ in range(30):
                if self._probe_remote_comfy(client):
                    return
                time.sleep(2)
            tail = self._exec(
                client,
                "tail -60 /root/autodl-tmp/comfyui_krea.log 2>/dev/null || true",
                timeout=10,
            )
            raise RuntimeError(f"ComfyUI 进程存在但未能就绪\n{tail}".strip())

        command = "cd /root/autodl-tmp/ComfyUI && setsid -f /bin/bash -c " + shlex.quote(
            "exec /root/miniconda3/bin/python main.py "
            "--listen 127.0.0.1 --port 8188 --cache-none "
            "> /root/autodl-tmp/comfyui_krea.log 2>&1"
        )
        self._exec(client, command, timeout=8)
        for _ in range(45):
            if self._probe_remote_comfy(client):
                return
            time.sleep(2)
        tail = self._exec(
            client,
            "tail -60 /root/autodl-tmp/comfyui_krea.log 2>/dev/null || true",
            timeout=10,
        )
        raise RuntimeError(f"ComfyUI 启动失败\n{tail}".strip())

    @staticmethod
    def _connect(config: GpuConnection) -> paramiko.SSHClient:
        if not config.password:
            raise ValueError("请输入 GPU 服务器 SSH 密码")
        last_error: Exception | None = None
        for attempt in range(1, 4):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    config.host,
                    port=config.port,
                    username=config.username,
                    password=config.password,
                    timeout=15,
                    banner_timeout=15,
                    auth_timeout=15,
                )
                return client
            except (
                EOFError,
                OSError,
                paramiko.SSHException,
            ) as exc:
                client.close()
                last_error = exc
                if attempt < 3:
                    time.sleep(attempt * 2)
        if last_error is not None:
            raise last_error
        raise RuntimeError("无法连接 GPU 服务器")

    @staticmethod
    def _exec(
        client: paramiko.SSHClient,
        command: str,
        *,
        timeout: int,
    ) -> str:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace")
        status = stdout.channel.recv_exit_status()
        if status:
            detail = (error or output).strip()
            raise RuntimeError(detail or f"远程命令失败，退出码 {status}")
        return output

    @staticmethod
    def _exec_streaming(
        client: paramiko.SSHClient,
        command: str,
        *,
        timeout: int,
        output_callback: Callable[[str], None] | None = None,
    ) -> str:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        lines: list[str] = []
        for raw in iter(stdout.readline, ""):
            line = raw.rstrip()
            lines.append(line)
            if output_callback:
                output_callback(line)
        error = stderr.read().decode("utf-8", "replace")
        status = stdout.channel.recv_exit_status()
        output = "\n".join(lines)
        if status:
            detail = (error or output).strip()
            raise RuntimeError(detail or f"远程命令失败，退出码 {status}")
        return output

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        name = type(exc).__name__
        if "Authentication" in name:
            return "SSH 认证失败，请检查密码"
        if "NoValidConnections" in name:
            return "无法连接 GPU 服务器，请确认实例已启动"
        return str(exc) or name

    @staticmethod
    def _to_int(value: str) -> int:
        try:
            return int(float(value.strip()))
        except (TypeError, ValueError):
            return 0


def default_gpu_connection() -> GpuConnection:
    return GpuConnection(
        host=os.getenv("GPU_SSH_HOST", "connect.nmb2.seetacloud.com"),
        port=int(os.getenv("GPU_SSH_PORT", "25518")),
        username=os.getenv("GPU_SSH_USER", "root"),
        password=os.getenv("GPU_SSH_PASSWORD", ""),
    )
