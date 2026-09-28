"""Load version-controlled prompt text packaged with the application."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    if not name or any(character in name for character in "/\\."):
        raise ValueError(f"无效提示词名称: {name!r}")
    prompt = files("app.prompts").joinpath(f"{name}.txt").read_text(encoding="utf-8")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError(f"提示词文件为空: {name}")
    return prompt
