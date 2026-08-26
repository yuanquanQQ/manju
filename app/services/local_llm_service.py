"""Lifecycle and health checks for the bundled local llama.cpp server."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.core.config import settings

MIN_COMPLETE_MODEL_BYTES = 4_000_000_000


@dataclass(slots=True)
class LocalLlmStatus:
    online: bool = False
    model_ready: bool = False
    model_ids: list[str] = field(default_factory=list)
    message: str = ""


class LocalLlmService:
    """Start and inspect the project's local OpenAI-compatible LLM."""

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = Path(model_path or settings.llm_model_path)
        self.log_path = settings.logs_dir / "local_llm.log"
        self.pid_path = settings.logs_dir / "local_llm.pid"

    def check_status(self, timeout: float = 3) -> LocalLlmStatus:
        status = LocalLlmStatus(model_ready=self._model_is_complete())
        url = f"{settings.llm_base_url.rstrip('/')}/v1/models"
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            status.model_ids = [
                str(item.get("id"))
                for item in payload.get("data", [])
                if isinstance(item, dict) and item.get("id")
            ]
            status.online = True
            status.message = (
                f"文本模型服务已就绪：{'、'.join(status.model_ids)}"
                if status.model_ids
                else "文本模型服务已就绪"
            )
            return status
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            pass

        if not self.model_path.exists():
            status.message = f"文本模型尚未下载：{self.model_path}"
        elif not status.model_ready:
            size_gb = self.model_path.stat().st_size / (1024**3)
            status.message = f"文本模型正在下载或文件不完整（当前 {size_gb:.2f} GB）"
        elif not self._is_local_endpoint():
            status.message = f"无法连接文本模型服务：{settings.llm_base_url}"
        else:
            status.message = "本地文本模型未启动"
        return status

    def start(self, timeout: int = 180) -> LocalLlmStatus:
        current = self.check_status()
        if current.online:
            return current
        if not self._is_local_endpoint():
            raise ValueError(
                f"当前 LLM 地址不是本机：{settings.llm_base_url}，请启动对应远程服务"
            )
        if not self._model_is_complete():
            raise FileNotFoundError(current.message)

        try:
            import llama_cpp  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "未安装本地 LLM 运行时，请安装 llama-cpp-python[server]"
            ) from exc

        parsed = urlparse(settings.llm_base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 1234
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "llama_cpp.server",
            "--model",
            str(self.model_path),
            "--model_alias",
            settings.llm_model,
            "--host",
            host,
            "--port",
            str(port),
            "--n_ctx",
            str(settings.llm_context_size),
            "--n_batch",
            "256",
            "--n_threads",
            str(max(4, (os.cpu_count() or 8) // 2)),
            "--n_gpu_layers",
            "0",
            "--chat_template_kwargs",
            json.dumps({"enable_thinking": False}),
            "--verbose",
            "False",
        ]
        creationflags = 0
        if sys.platform == "win32":
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        with self.log_path.open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=settings.project_root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True,
            )
        self.pid_path.write_text(str(process.pid), encoding="utf-8")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = self._log_tail()
                raise RuntimeError(
                    f"本地文本模型启动失败，退出码 {process.returncode}\n{detail}"
                )
            status = self.check_status(timeout=2)
            if status.online:
                return status
            time.sleep(2)
        raise TimeoutError(
            f"本地文本模型在 {timeout} 秒内未就绪，请查看 {self.log_path}"
        )

    def _model_is_complete(self) -> bool:
        return (
            self.model_path.is_file()
            and self.model_path.stat().st_size >= MIN_COMPLETE_MODEL_BYTES
        )

    @staticmethod
    def _is_local_endpoint() -> bool:
        host = (urlparse(settings.llm_base_url).hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}

    def _log_tail(self, line_count: int = 40) -> str:
        if not self.log_path.exists():
            return ""
        return "\n".join(
            self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[
                -line_count:
            ]
        )
