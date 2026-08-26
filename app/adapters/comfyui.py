"""ComfyUI API 适配器。

通过 HTTP 提交工作流，WebSocket 监听进度，HTTP 轮询兜底，下载产出图片。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import requests
import websocket  # type: ignore[import-untyped]

from app.core.logger import logger

DEFAULT_COMFY_URL = "localhost:8189"


class ComfyUIClient:
    """ComfyUI 客户端：提交工作流 → 等待执行 → 下载结果。"""

    def __init__(
        self,
        base_url: str = DEFAULT_COMFY_URL,
        timeout: int = 600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── 连接检查 ──────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        r = requests.get(f"http://{self.base_url}/system_stats", timeout=10)
        r.raise_for_status()
        return r.json()

    def upload_image(self, image_path: str, *, subfolder: str = "") -> str:
        """上传图片到 ComfyUI input 目录，返回文件名。"""
        name = Path(image_path).name
        with open(image_path, "rb") as f:
            r = requests.post(
                f"http://{self.base_url}/upload/image",
                files={"image": (name, f, "image/png")},
                data={"subfolder": subfolder, "overwrite": "true"},
                timeout=30,
            )
        r.raise_for_status()
        result = r.json() if r.text.strip() else {}
        uploaded_name = result.get("name", name)
        logger.info(f"已上传图片: {uploaded_name}")
        return uploaded_name

    def check_models(self) -> dict[str, list[str]]:
        """返回 {node_class: [model_names]} 用于校验。"""
        info = requests.get(
            f"http://{self.base_url}/object_info", timeout=10
        ).json()
        result: dict[str, list[str]] = {}
        for name, node in info.items():
            inp = node.get("input", {}).get("required", {})
            for param, spec in inp.items():
                if isinstance(spec, list) and len(spec) >= 2:
                    meta = spec[1]
                    if isinstance(meta, dict) and "options" in meta:
                        result.setdefault(name, []).extend(meta["options"])
        return result

    # ── 提交工作流 ────────────────────────────────────────

    def submit(
        self,
        workflow: dict[str, Any],
        *,
        client_id: str | None = None,
    ) -> str:
        """提交工作流，返回 prompt_id。"""
        cid = client_id or str(uuid.uuid4())
        payload = {"prompt": workflow, "client_id": cid}
        r = requests.post(
            f"http://{self.base_url}/prompt",
            json=payload,
            timeout=30,
        )
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = {}
            raise RuntimeError(
                f"ComfyUI 提交失败 (HTTP {r.status_code}): "
                f"{json.dumps(body, ensure_ascii=False)[:1000]}"
            )
        data = r.json()
        if "prompt_id" not in data:
            raise RuntimeError(f"ComfyUI 返回异常: {data}")
        prompt_id = data["prompt_id"]
        logger.info(f"ComfyUI 已提交: prompt_id={prompt_id}")
        return prompt_id

    # ── 执行等待 ──────────────────────────────────────────

    def wait(
        self,
        prompt_id: str,
        *,
        poll_interval: float = 2.0,
        ws_timeout: float = 15.0,
    ) -> dict[str, Any]:
        """阻塞等待 prompt 执行完成，优先 WebSocket，失败回退到 HTTP 轮询。"""
        try:
            return self._wait_ws(prompt_id, timeout=ws_timeout)
        except Exception as exc:
            logger.warning(f"WebSocket 等待失败，回退 HTTP 轮询: {exc}")

        return self._wait_poll(prompt_id, poll_interval=poll_interval)

    def _wait_ws(
        self,
        prompt_id: str,
        *,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """通过 WebSocket 等待执行完成。"""
        ws_url = f"ws://{self.base_url}/ws?clientId=ws_wait_{uuid.uuid4().hex[:8]}"
        result: dict[str, Any] = {}
        done = False

        def on_message(_ws, raw: str) -> None:
            nonlocal done, result
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                return
            if msg.get("type") == "executing":
                data = msg.get("data", {})
                if data.get("prompt_id") == prompt_id and data.get("node") is None:
                    done = True
            elif msg.get("type") == "execution_error":
                data = msg.get("data", {})
                if data.get("prompt_id") == prompt_id:
                    result["error"] = data
                    done = True

        def on_error(_ws, err) -> None:
            nonlocal done
            logger.warning(f"WebSocket 错误: {err}")
            done = True

        ws = websocket.WebSocket()
        ws.settimeout(timeout)
        ws.connect(ws_url)
        try:
            while not done:
                raw = ws.recv()
                on_message(ws, raw)
        finally:
            ws.close()

        if "error" in result:
            raise RuntimeError(
                f"ComfyUI 执行错误: {json.dumps(result['error'], ensure_ascii=False)[:500]}"
            )
        return self._fetch_history(prompt_id)

    def _wait_poll(
        self,
        prompt_id: str,
        *,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        """HTTP 轮询等待执行完成。"""
        start = time.time()
        last_log = start
        while (time.time() - start) < self.timeout:
            history = self._fetch_history(prompt_id)
            if history:
                status = history.get("status", {})
                if not status.get("completed", True):
                    elapsed = int(time.time() - start)
                    if time.time() - last_log > 5:
                        logger.info(f"ComfyUI 执行中... ({elapsed}s, 状态: {status.get('status_str', 'unknown')})")
                        last_log = time.time()
                    time.sleep(poll_interval)
                    continue
                return history
            elapsed = int(time.time() - start)
            if time.time() - last_log > 5:
                logger.info(f"ComfyUI 排队中... ({elapsed}s)")
                last_log = time.time()
            time.sleep(poll_interval)
        raise TimeoutError(
            f"ComfyUI 执行超时 ({self.timeout}s): prompt_id={prompt_id}"
        )

    def _fetch_history(self, prompt_id: str) -> dict[str, Any]:
        r = requests.get(
            f"http://{self.base_url}/history/{prompt_id}",
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get(prompt_id, {})

    # ── 下载结果 ──────────────────────────────────────────

    def download_images(
        self,
        history: dict[str, Any],
        output_dir: str | Path,
        *,
        filename_prefix: str = "image",
    ) -> list[Path]:
        """从 history 中提取 SaveImage 节点的产出并下载。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []

        outputs = history.get("outputs", {})
        for node_id, node_output in outputs.items():
            images = node_output.get("images", [])
            for img in images:
                filename = img.get("filename", "")
                subfolder = img.get("subfolder", "")
                img_type = img.get("type", "output")
                params = {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": img_type,
                }
                r = requests.get(
                    f"http://{self.base_url}/view",
                    params=params,
                    timeout=60,
                )
                r.raise_for_status()
                stem = Path(filename).stem
                dest = out / f"{filename_prefix}_{stem}.png"
                dest.write_bytes(r.content)
                saved.append(dest)
                logger.info(f"已下载: {dest} ({len(r.content)} bytes)")

        return saved

    # ── 便捷：一键提交 + 等待 + 下载 ──────────────────────

    def generate(
        self,
        workflow: dict[str, Any],
        output_dir: str | Path,
        *,
        filename_prefix: str = "image",
    ) -> list[Path]:
        prompt_id = self.submit(workflow)
        history = self.wait(prompt_id)
        return self.download_images(
            history,
            output_dir,
            filename_prefix=filename_prefix,
        )
