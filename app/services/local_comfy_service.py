"""Run the existing generation workflows against a local ComfyUI instance."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import settings
from app.services.gpu_service import GenerationResult
from app.services.image_models import (
    IMAGE_MODEL_PRESETS,
    validate_image_model_ids,
)
from app.services.model_runtime_service import LocalModelRuntimeService


class LocalComfyGenerationService:
    """Local counterpart of the SSH image-generation adapter."""

    def __init__(
        self,
        runtime: LocalModelRuntimeService | None = None,
    ) -> None:
        self.runtime = runtime or LocalModelRuntimeService()

    def generate_character(
        self,
        *,
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
        started = time.monotonic()
        selected = validate_image_model_ids(model_ids)
        workflow = settings.workflows_dir / "krea" / "generate_samples.py"
        episode = Path(episode_path).resolve()
        output = Path(local_output_dir).resolve()
        if not workflow.is_file():
            raise FileNotFoundError(f"本地生图工作流不存在：{workflow}")
        if not episode.is_file():
            raise FileNotFoundError(f"分镜文件不存在：{episode}")
        self._ensure_callable(selected)
        output.mkdir(parents=True, exist_ok=True)

        command = [
            sys.executable,
            str(workflow),
            "--episode",
            str(episode),
            "--output-dir",
            str(output),
            "--comfy-url",
            self.runtime.comfy_url,
            "--character",
            character,
            "--portrait-count",
            str(max(1, min(count, 8))),
            "--seed",
            str(seed),
            "--prompt-override",
            prompt,
            "--style-prompt",
            style_prompt,
            "--layout-preset",
            layout_preset,
        ]
        for model_id in selected:
            command.extend(("--model", model_id))

        total = max(1, count * len(selected))

        def on_output(line: str) -> None:
            match = re.search(r"\[PROGRESS]\s+(\d+)/(\d+)", line)
            if not match or not progress_callback:
                return
            done = int(match.group(1))
            reported_total = max(1, int(match.group(2)) or total)
            labels = "、".join(
                IMAGE_MODEL_PRESETS[model_id].label
                for model_id in selected
            )
            progress_callback(
                5 + int(done / reported_total * 90),
                f"本机已生成 {done}/{reported_total} 张（{labels}）",
            )

        if progress_callback:
            progress_callback(2, "正在调用本机 ComfyUI")
        log = self._run_streaming(
            command,
            timeout=max(900, 900 * total),
            output_callback=on_output,
        )
        manifest_path = output / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError("本机工作流完成，但没有生成 manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        images = [
            output / str(record.get("file") or "")
            for record in (manifest.get("images") or [])
            if isinstance(record, dict)
            and (output / str(record.get("file") or "")).is_file()
        ]
        if not images:
            raise RuntimeError("本机工作流完成，但没有找到生成图片")
        elapsed = time.monotonic() - started
        if progress_callback:
            progress_callback(100, f"本机已生成 {len(images)} 张候选")
        return GenerationResult(
            local_dir=output,
            images=images,
            manifest=manifest,
            remote_log=log,
            elapsed_seconds=elapsed,
        )

    def _ensure_callable(self, model_ids: list[str]) -> None:
        inventory = self.runtime.check_status()
        statuses = {model.model_id: model for model in inventory.models}
        unavailable = [
            f"{IMAGE_MODEL_PRESETS[model_id].label}："
            f"{statuses[model_id].message if model_id in statuses else '未检测'}"
            for model_id in model_ids
            if model_id not in statuses or not statuses[model_id].callable
        ]
        if unavailable:
            raise RuntimeError(
                "本机模型暂不可调用："
                + "；".join(unavailable)
                + f"。请在“连接与设置”检测本机模型（{inventory.model_root}）。"
            )

    @staticmethod
    def _run_streaming(
        command: list[str],
        *,
        timeout: int,
        output_callback: Callable[[str], None] | None = None,
    ) -> str:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        started = time.monotonic()
        lines: list[str] = []
        assert process.stdout is not None
        while True:
            line = process.stdout.readline()
            if line:
                cleaned = line.rstrip()
                lines.append(cleaned)
                if output_callback:
                    output_callback(cleaned)
            elif process.poll() is not None:
                break
            if time.monotonic() - started > timeout:
                process.kill()
                raise TimeoutError(f"本机 ComfyUI 任务超过 {timeout} 秒")
        return_code = process.wait()
        detail = "\n".join(lines)
        if return_code:
            raise RuntimeError(
                f"本机 ComfyUI 工作流失败（退出码 {return_code}）\n"
                f"{detail[-4000:]}"
            )
        return detail
