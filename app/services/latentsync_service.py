"""Remote LatentSync 1.6 installation, detection, and inference."""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from loguru import logger

from app.core.config import settings
from app.core.files import atomic_write_json, sha256_text
from app.database.db import init_db
from app.domain.jobs import JobStatus
from app.services.artifact_service import register_artifact
from app.services.gpu_service import GpuConnection, GpuServerService
from app.services.job_service import create_job, transition_job


@dataclass(slots=True)
class LatentSyncStatus:
    ssh_online: bool = False
    installed: bool = False
    installing: bool = False
    callable: bool = False
    version: str = "LatentSync 1.6"
    checkpoint_size_gb: float = 0.0
    gpu_name: str = ""
    memory_total_mb: int = 0
    message: str = ""


@dataclass(slots=True)
class LatentSyncResult:
    episode_number: int
    shot_number: int
    video_path: Path
    manifest_path: Path
    audio_path: Path
    source_video: Path
    job_id: str
    elapsed_seconds: float
    face_match_similarity: float = 0.0


class LatentSyncRemoteService:
    """Call the official ByteDance LatentSync CLI over the existing SSH link."""

    source_dir = "/root/autodl-tmp/LatentSync"
    env_dir = "/root/autodl-tmp/latentsync-env"
    script_dir = "/root/autodl-tmp/manju/scripts/gpu/latentsync"
    request_root = "/root/autodl-tmp/manju/inputs/latentsync_app"

    def __init__(self, gpu_service: GpuServerService | None = None) -> None:
        self.gpu_service = gpu_service or GpuServerService()

    def check_status(self, config: GpuConnection) -> LatentSyncStatus:
        status = LatentSyncStatus()
        try:
            client = self.gpu_service._connect(config)
        except Exception as exc:
            status.message = self.gpu_service._friendly_error(exc)
            return status
        status.ssh_online = True
        command = f"""
python={shlex.quote(self.env_dir + '/bin/python')}
source={shlex.quote(self.source_dir)}
run={shlex.quote(self.script_dir + '/run.sh')}
installed=0
if [ -x "$python" ] && [ -f "$source/scripts/inference.py" ] && \
   [ -f "$source/checkpoints/latentsync_unet.pt" ] && \
   [ -f "$source/checkpoints/whisper/tiny.pt" ] && [ -x "$run" ] && \
   [ -f {shlex.quote(self.script_dir + '/face_detector.py')} ]; then
  installed=1
fi
pid=$(cat /root/autodl-tmp/latentsync_install.pid 2>/dev/null || true)
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then installing=1; else installing=0; fi
gpu=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits \
  2>/dev/null | head -1)
size=$(stat -c %s "$source/checkpoints/latentsync_unet.pt" 2>/dev/null || echo 0)
import_ok=0
if [ "$installed" = 1 ]; then
  "$python" -c 'import torch, diffusers, decord, mediapipe' \
    >/dev/null 2>&1 && import_ok=1 || true
fi
printf '%s\n%s\n%s\n%s\n%s\n' "$installed" "$installing" "$gpu" "$size" "$import_ok"
"""
        try:
            lines = self.gpu_service._exec(
                client,
                command,
                timeout=45,
            ).splitlines()
            status.installed = bool(lines and lines[0] == "1")
            status.installing = len(lines) > 1 and lines[1] == "1"
            gpu = [part.strip() for part in (lines[2] if len(lines) > 2 else "").split(",")]
            if gpu:
                status.gpu_name = gpu[0]
            if len(gpu) > 1:
                status.memory_total_mb = self._to_int(gpu[1])
            checkpoint_bytes = self._to_int(lines[3] if len(lines) > 3 else "0")
            status.checkpoint_size_gb = round(
                checkpoint_bytes / 1024**3,
                2,
            )
            imports_ready = len(lines) > 4 and lines[4] == "1"
            status.callable = (
                status.installed
                and imports_ready
                and status.memory_total_mb >= 18 * 1024
            )
            if status.callable:
                status.message = "LatentSync 1.6 已就绪"
            elif status.installing:
                status.message = "LatentSync 1.6 正在安装"
            elif status.installed and status.memory_total_mb < 18 * 1024:
                status.message = "模型已安装，但显存不足 18GB"
            elif status.installed:
                status.message = "文件已安装，但 Python 依赖检测失败"
            else:
                status.message = "LatentSync 1.6 尚未安装"
        except Exception as exc:
            status.message = str(exc)
        finally:
            client.close()
        return status

    def deploy(
        self,
        config: GpuConnection,
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> LatentSyncStatus:
        script_root = settings.project_root / "scripts" / "gpu" / "latentsync"
        names = ("install.sh", "run.sh", "face_detector.py")
        missing = [name for name in names if not (script_root / name).is_file()]
        if missing:
            raise FileNotFoundError(
                f"LatentSync 部署文件缺失：{', '.join(missing)}"
            )

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        report(2, "正在连接 GPU 服务器")
        client = self.gpu_service._connect(config)
        try:
            self.gpu_service._exec(
                client,
                f"mkdir -p {shlex.quote(self.script_dir)}",
                timeout=15,
            )
            sftp = client.open_sftp()
            try:
                for name in names:
                    sftp.put(
                        str(script_root / name),
                        f"{self.script_dir}/{name}",
                    )
            finally:
                sftp.close()
            self.gpu_service._exec(
                client,
                f"chmod +x {shlex.quote(self.script_dir)}/*.sh",
                timeout=15,
            )
            report(5, "正在安装独立环境和 LatentSync 1.6 权重")

            def on_output(line: str) -> None:
                lowered = line.lower()
                if "successfully installed" in lowered:
                    report(55, "Python 与 CUDA 依赖已安装")
                elif "latentsync_unet.pt" in lowered:
                    report(72, "正在通过 HF 镜像下载 1.6 权重")
                elif "stable diffusion vae cache ready" in lowered:
                    report(94, "VAE 缓存已准备")

            self.gpu_service._exec_streaming(
                client,
                (
                    "env HF_ENDPOINT=https://hf-mirror.com "
                    f"{shlex.quote(self.script_dir + '/install.sh')}"
                ),
                timeout=14400,
                output_callback=on_output,
            )
        finally:
            client.close()
        status = self.check_status(config)
        if not status.callable:
            raise RuntimeError(status.message)
        report(100, status.message)
        return status

    def synchronize(
        self,
        config: GpuConnection,
        project_root: Path,
        *,
        episode_number: int,
        shot_number: int,
        source_video: Path,
        audio_path: Path,
        inference_steps: int = 20,
        guidance_scale: float = 1.5,
        target_character: str = "",
        face_reference: Path | None = None,
        face_selection_mode: str = "auto_single_face",
        minimum_face_similarity: float = 0.18,
        restore_comfy: bool = True,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> LatentSyncResult:
        root = Path(project_root).resolve()
        video = self._project_file(root, source_video, "口型源视频")
        audio = self._project_file(root, audio_path, "口型配音")
        target = target_character.strip()
        selection_mode = face_selection_mode.strip() or "auto_single_face"
        if selection_mode not in {"auto_single_face", "speaker_tracking"}:
            raise ValueError(f"尚未支持的目标脸选择模式：{selection_mode}")
        reference = (
            self._project_file(root, face_reference, "说话人定妆照")
            if face_reference is not None
            else self._selected_cast_reference(root, target)
            if selection_mode == "speaker_tracking" and target
            else None
        )
        if selection_mode == "speaker_tracking":
            if not target:
                raise ValueError("按说话人跟踪时必须指定目标人物")
            if reference is None:
                raise ValueError(f"目标人物“{target}”尚未选择定妆照")
        minimum_similarity = max(0.0, min(float(minimum_face_similarity), 0.95))
        steps = max(10, min(int(inference_steps), 50))
        guidance = max(1.0, min(float(guidance_scale), 3.0))
        status = self.check_status(config)
        if not status.callable:
            raise RuntimeError(status.message)

        init_db(root / "database" / "world.db")
        payload = {
            "episode_number": episode_number,
            "shot_number": shot_number,
            "source_video": video.relative_to(root).as_posix(),
            "audio_file": audio.relative_to(root).as_posix(),
            "engine": "latentsync_1_6",
            "inference_steps": steps,
            "guidance_scale": guidance,
            "duration_policy": "match_source_video",
            "target_character": target,
            "face_selection_mode": selection_mode,
            "face_reference": (
                reference.relative_to(root).as_posix() if reference else ""
            ),
            "minimum_face_similarity": minimum_similarity,
        }
        job = create_job(
            "shot_lip_sync",
            payload=payload,
            input_hash=sha256_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
            reuse_existing=False,
        )
        transition_job(job.id, JobStatus.RUNNING)
        started = time.monotonic()
        run_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        remote_dir = f"{self.request_root}/{run_id}"
        remote_video = f"{remote_dir}/input{video.suffix.lower()}"
        remote_audio = f"{remote_dir}/audio{audio.suffix.lower()}"
        remote_reference = (
            f"{remote_dir}/face_reference{reference.suffix.lower()}"
            if reference is not None
            else ""
        )
        remote_output = f"{remote_dir}/output.mp4"
        output_dir = (
            root
            / "production"
            / "videos"
            / f"episode_{episode_number:03d}"
            / f"shot_{shot_number:03d}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"shot_{shot_number:03d}_lipsync_{run_id}.mp4"
        manifest_path = output_dir / f"manifest_lipsync_{run_id}.json"
        comfy_was_online = False
        sync_succeeded = False

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        client = self.gpu_service._connect(config)
        try:
            try:
                comfy_was_online = "ONLINE" in self.gpu_service._exec(
                    client,
                    (
                        "curl -fsS --max-time 3 "
                        "http://127.0.0.1:8188/system_stats "
                        ">/dev/null 2>&1 && echo ONLINE || true"
                    ),
                    timeout=8,
                )
            except Exception:
                # A failed probe must not block lip-sync inference. In that case
                # leave ComfyUI in its current state rather than guessing.
                comfy_was_online = False
            report(3, "正在上传视频和配音")
            self.gpu_service._exec(
                client,
                f"mkdir -p {shlex.quote(remote_dir)}",
                timeout=15,
            )
            sftp = client.open_sftp()
            try:
                sftp.put(str(video), remote_video)
                sftp.put(str(audio), remote_audio)
                if reference is not None:
                    sftp.put(str(reference), remote_reference)
            finally:
                sftp.close()
            report(12, "正在释放 ComfyUI/CosyVoice 显存并加载 LatentSync")
            command = " ".join(
                [
                    "env",
                    "HF_ENDPOINT=https://hf-mirror.com",
                    shlex.quote(self.script_dir + "/run.sh"),
                    shlex.quote(remote_video),
                    shlex.quote(remote_audio),
                    shlex.quote(remote_output),
                    str(steps),
                    f"{guidance:.2f}",
                    shlex.quote(remote_reference),
                    f"{minimum_similarity:.3f}",
                ]
            )

            def on_output(line: str) -> None:
                lowered = line.lower()
                if "loaded checkpoint" in lowered:
                    report(28, "LatentSync 1.6 权重已加载")
                elif "audio" in lowered and "feature" in lowered:
                    report(42, "正在提取配音特征")
                elif "%" in line or "it/s" in line:
                    report(65, "正在生成同步口型")
                elif "latentsync_output=" in lowered:
                    report(92, "口型视频已生成，正在下载")

            remote_log = self.gpu_service._exec_streaming(
                client,
                command,
                timeout=7200,
                output_callback=on_output,
            )
            match = re.search(
                r"SPEAKER_FACE_MATCH\s+similarity=([-+]?\d+(?:\.\d+)?)",
                remote_log,
            )
            face_match_similarity = float(match.group(1)) if match else 0.0
            temporary = destination.with_suffix(".mp4.part")
            sftp = client.open_sftp()
            try:
                sftp.get(remote_output, str(temporary))
            finally:
                sftp.close()
            if temporary.stat().st_size < 1024:
                raise RuntimeError("LatentSync 返回的视频文件为空")
            os.replace(temporary, destination)
            elapsed = time.monotonic() - started
            metadata = {
                "schema_version": "1.0",
                "kind": "shot_lip_sync",
                "engine": "latentsync_1_6",
                "episode_number": episode_number,
                "shot_number": shot_number,
                "source_video": video.relative_to(root).as_posix(),
                "audio_file": audio.relative_to(root).as_posix(),
                "output_file": destination.relative_to(root).as_posix(),
                "inference_steps": steps,
                "guidance_scale": guidance,
                "duration_policy": "match_source_video",
                "target_character": target,
                "face_selection_mode": selection_mode,
                "face_reference": (
                    reference.relative_to(root).as_posix() if reference else ""
                ),
                "minimum_face_similarity": minimum_similarity,
                "face_match_similarity": face_match_similarity,
                "generated_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "elapsed_seconds": round(elapsed, 3),
                "job_id": job.id,
            }
            atomic_write_json(manifest_path, metadata)
            register_artifact(
                root,
                destination,
                kind="shot_lip_sync",
                job_id=job.id,
                metadata=metadata,
            )
            transition_job(
                job.id,
                JobStatus.SUCCEEDED,
                result=metadata,
            )
            sync_succeeded = True
            report(96, "LatentSync 口型视频已完成，正在恢复生成服务")
            return LatentSyncResult(
                episode_number=episode_number,
                shot_number=shot_number,
                video_path=destination,
                manifest_path=manifest_path,
                audio_path=audio,
                source_video=video,
                job_id=job.id,
                elapsed_seconds=elapsed,
                face_match_similarity=face_match_similarity,
            )
        except Exception as exc:
            transition_job(
                job.id,
                JobStatus.FAILED,
                error_code="latentsync_failed",
                error_message=str(exc),
            )
            raise
        finally:
            try:
                self.gpu_service._exec(
                    client,
                    f"rm -rf -- {shlex.quote(remote_dir)}",
                    timeout=15,
                )
            except Exception as exc:
                logger.warning("LatentSync 远程临时目录清理失败: {}", exc)
            finally:
                client.close()
            restore_error: Exception | None = None
            if comfy_was_online and restore_comfy:
                try:
                    self.gpu_service.start_comfy(config)
                except Exception as exc:
                    restore_error = exc
                    logger.warning("LatentSync 完成后恢复 ComfyUI 失败: {}", exc)
            if sync_succeeded:
                if restore_error is not None:
                    report(100, "口型视频已完成，但 ComfyUI 自动恢复失败")
                elif comfy_was_online and restore_comfy:
                    report(100, "口型视频已完成，ComfyUI 已恢复")
                else:
                    report(100, "LatentSync 口型视频已完成")

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

    @staticmethod
    def _selected_cast_reference(root: Path, character: str) -> Path | None:
        if not character:
            return None
        selection_path = root / "production" / "cast_selection.json"
        if not selection_path.is_file():
            return None
        try:
            value = json.loads(selection_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        selections = value.get("selections") if isinstance(value, dict) else None
        configured = selections.get(character) if isinstance(selections, dict) else None
        if not configured:
            return None
        candidate = (root / str(configured)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _to_int(value: str) -> int:
        try:
            return int(float(value.strip()))
        except (TypeError, ValueError):
            return 0
