"""Remote CosyVoice 3 synthesis through the existing SSH connection."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.domain.audio import DubbingLineSpec
from app.services.gpu_service import GpuConnection, GpuServerService


@dataclass(slots=True)
class CosyVoiceStatus:
    ssh_online: bool = False
    installed: bool = False
    online: bool = False
    model: str = ""
    model_dir: str = ""
    gpu: str = ""
    message: str = ""


class CosyVoiceRemoteService:
    """Manage and call the private CosyVoice service on the GPU server."""

    service_dir = "/root/cosyvoice-service"
    request_root = f"{service_dir}/requests"
    health_url = "http://127.0.0.1:50000/health"
    synthesis_url = "http://127.0.0.1:50000/synthesize"

    def __init__(self, gpu_service: GpuServerService | None = None) -> None:
        self.gpu_service = gpu_service or GpuServerService()

    def check_status(self, config: GpuConnection) -> CosyVoiceStatus:
        status = CosyVoiceStatus()
        try:
            client = self.gpu_service._connect(config)
        except Exception as exc:
            status.message = self.gpu_service._friendly_error(exc)
            return status
        status.ssh_online = True
        try:
            probe = self.gpu_service._exec(
                client,
                (
                    f"test -x {self.service_dir}/start.sh && "
                    "test -x /root/cosyvoice-env/bin/python && "
                    "test -f /root/cosyvoice-models/Fun-CosyVoice3-0.5B/"
                    "cosyvoice3.yaml && echo INSTALLED || true; "
                    f"curl -fsS --max-time 5 {self.health_url} || true"
                ),
                timeout=12,
            ).splitlines()
            status.installed = bool(probe and probe[0] == "INSTALLED")
            payload_line = next(
                (line for line in reversed(probe) if line.lstrip().startswith("{")),
                "",
            )
            if payload_line:
                payload = json.loads(payload_line)
                status.online = bool(payload.get("ok"))
                status.model = str(payload.get("model") or "")
                status.model_dir = str(payload.get("model_dir") or "")
                status.gpu = str(payload.get("gpu") or "")
            if status.online:
                status.message = f"{status.model} 已就绪"
            elif status.installed:
                status.message = "CosyVoice 已安装但尚未启动"
            else:
                status.message = "CosyVoice 尚未安装"
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
    ) -> CosyVoiceStatus:
        """Upload the bundled installer and deploy a reproducible server runtime."""

        script_dir = settings.project_root / "scripts" / "gpu" / "cosyvoice"
        names = ("install.sh", "start.sh", "download_model.py", "server.py")
        missing = [name for name in names if not (script_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(
                f"CosyVoice 部署文件缺失：{', '.join(missing)}"
            )

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        report(2, "正在连接 GPU 服务器")
        client = self.gpu_service._connect(config)
        try:
            self.gpu_service._exec(
                client,
                f"mkdir -p {self.service_dir}",
                timeout=15,
            )
            sftp = client.open_sftp()
            try:
                for name in names:
                    sftp.put(
                        str(script_dir / name),
                        f"{self.service_dir}/{name}",
                    )
            finally:
                sftp.close()
            self.gpu_service._exec(
                client,
                (
                    f"chmod +x {self.service_dir}/install.sh "
                    f"{self.service_dir}/start.sh"
                ),
                timeout=15,
            )
            report(5, "部署文件已上传，正在安装独立环境")

            def on_output(line: str) -> None:
                if "Successfully installed" in line:
                    report(42, "Python 与 CUDA 推理依赖已安装")
                elif "[model] downloading" in line:
                    report(48, "正在通过 HF 镜像下载 CosyVoice 3")
                elif "[model] ready" in line:
                    report(88, "CosyVoice 3 模型已下载并校验")

            self.gpu_service._exec_streaming(
                client,
                (
                    f"env HF_ENDPOINT=https://hf-mirror.com "
                    f"{self.service_dir}/install.sh"
                ),
                timeout=10800,
                output_callback=on_output,
            )
        finally:
            client.close()
        report(92, "正在启动 CosyVoice 3")
        status = self.start(config)
        report(100, status.message)
        return status

    def start(self, config: GpuConnection) -> CosyVoiceStatus:
        client = self.gpu_service._connect(config)
        try:
            self.gpu_service._stop_remote_comfy(client, require_idle=True)
            self.gpu_service._exec(
                client,
                f"env HF_ENDPOINT=https://hf-mirror.com {self.service_dir}/start.sh",
                timeout=20,
            )
        finally:
            client.close()
        for attempt in range(90):
            status = self.check_status(config)
            if status.online:
                return status
            if attempt >= 2 and not self._remote_process_alive(config):
                break
            time.sleep(2)
        status = self.check_status(config)
        client = self.gpu_service._connect(config)
        try:
            log = self.gpu_service._exec(
                client,
                f"tail -80 {self.service_dir}/cosyvoice.log 2>/dev/null || true",
                timeout=15,
            )
        finally:
            client.close()
        raise RuntimeError(f"{status.message}\n{log}".strip())

    def _remote_process_alive(self, config: GpuConnection) -> bool:
        client = self.gpu_service._connect(config)
        try:
            output = self.gpu_service._exec(
                client,
                (
                    f"pid=$(cat {self.service_dir}/cosyvoice.pid "
                    "2>/dev/null || true); "
                    'test -n "$pid" && kill -0 "$pid" 2>/dev/null '
                    "&& echo ALIVE || true"
                ),
                timeout=10,
            )
            return "ALIVE" in output
        finally:
            client.close()

    def ensure_online(self, config: GpuConnection) -> CosyVoiceStatus:
        status = self.check_status(config)
        if status.online:
            return status
        if not status.installed:
            raise RuntimeError(
                "GPU 服务器尚未安装 CosyVoice，请在“连接与设置”中完成部署。"
            )
        return self.start(config)

    def stop(self, config: GpuConnection) -> CosyVoiceStatus:
        """Release CosyVoice GPU memory before image or video generation."""

        client = self.gpu_service._connect(config)
        try:
            command = (
                f"pid_file={self.service_dir}/cosyvoice.pid; "
                'pid=$(cat "$pid_file" 2>/dev/null || true); '
                'case "$pid" in (*[!0-9]*|"") exit 0;; esac; '
                'kill -TERM "$pid" 2>/dev/null || true; '
                "for _ in $(seq 1 30); do "
                'kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done'
            )
            self.gpu_service._exec(client, command, timeout=25)
        finally:
            client.close()
        return self.check_status(config)

    def synthesize(
        self,
        config: GpuConnection,
        spec: DubbingLineSpec,
        destination: Path,
        *,
        progress_callback: Callable[[str], None] | None = None,
    ) -> Path:
        reference = Path(spec.reference_audio or "")
        if not reference.is_file():
            raise FileNotFoundError(f"CosyVoice 参考音频不存在：{reference}")
        if not spec.reference_text.strip():
            raise ValueError("CosyVoice 需要填写参考音频对应台词")

        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        request_id = uuid4().hex
        remote_dir = f"{self.request_root}/{request_id}"
        reference_suffix = reference.suffix.lower()
        if reference_suffix not in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
            reference_suffix = ".wav"
        remote_reference = f"{remote_dir}/reference{reference_suffix}"
        remote_output = f"{remote_dir}/output.wav"
        text_files = {
            "tts_text": spec.text,
            "prompt_text": spec.reference_text,
            "instruct_text": spec.instruct_text,
        }
        client = self.gpu_service._connect(config)
        try:
            self.gpu_service._exec(
                client,
                f"mkdir -p {shlex.quote(remote_dir)}",
                timeout=15,
            )
            sftp = client.open_sftp()
            try:
                sftp.put(str(reference), remote_reference)
                for field, value in text_files.items():
                    remote_path = f"{remote_dir}/{field}.txt"
                    with sftp.file(remote_path, "wb") as handle:
                        handle.write(value.encode("utf-8"))
            finally:
                sftp.close()
            if progress_callback:
                progress_callback("参考音频已上传，正在进行本地音色克隆")
            speed = self._rate_to_speed(spec.rate)
            profile_id = self._voice_profile_id(reference, spec.reference_text)
            fields = " ".join(
                (
                    f"-F {shlex.quote(f'tts_text=<{remote_dir}/tts_text.txt')}",
                    f"-F {shlex.quote(f'prompt_text=<{remote_dir}/prompt_text.txt')}",
                    f"-F {shlex.quote(f'instruct_text=<{remote_dir}/instruct_text.txt')}",
                    f"-F {shlex.quote(f'voice_profile_id={profile_id}')}",
                    f"-F {shlex.quote(f'speed={speed:.3f}')}",
                    f"-F {shlex.quote(f'prompt_wav=@{remote_reference}')}",
                )
            )
            command = (
                "curl -fsS --show-error --max-time 900 -X POST "
                f"{fields} {shlex.quote(self.synthesis_url)} "
                f"-o {shlex.quote(remote_output)}; "
                f"test -s {shlex.quote(remote_output)}"
            )
            self.gpu_service._exec(client, command, timeout=930)
            temporary = destination.with_suffix(destination.suffix + ".part")
            sftp = client.open_sftp()
            try:
                sftp.get(remote_output, str(temporary))
            finally:
                sftp.close()
            if temporary.stat().st_size < 64:
                raise RuntimeError("CosyVoice 返回的音频文件为空")
            header = temporary.read_bytes()[:12]
            if not (header.startswith(b"RIFF") and header[8:12] == b"WAVE"):
                raise RuntimeError("CosyVoice 返回了无效的 WAV 文件")
            os.replace(temporary, destination)
            return destination
        finally:
            try:
                self.gpu_service._exec(
                    client,
                    f"rm -rf -- {shlex.quote(remote_dir)}",
                    timeout=15,
                )
            finally:
                client.close()

    @staticmethod
    def _rate_to_speed(rate: str) -> float:
        try:
            percent = int(rate.strip().removesuffix("%"))
        except ValueError:
            percent = 0
        return max(0.6, min(1.6, 1.0 + percent / 100))

    @staticmethod
    def _voice_profile_id(reference: Path, reference_text: str) -> str:
        digest = hashlib.sha256()
        digest.update(reference.read_bytes())
        digest.update(reference_text.encode("utf-8"))
        return f"manju_{digest.hexdigest()[:24]}"
