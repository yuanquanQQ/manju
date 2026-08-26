"""OpenAI Chat Completions 兼容的结构化 LLM 适配器。"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

import requests

from app.core.config import settings


class StructuredLLM(Protocol):
    @property
    def model_name(self) -> str: ...

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]: ...


def extract_json_object(text: str) -> dict[str, Any]:
    """从可能带解释、think 块或 Markdown 围栏的回复中提取合法 JSON 对象。

    优先取最后一个合法 JSON 对象（避免取到思考过程中的碎片 JSON）。
    """
    stripped = text.strip()

    # 去除 qwen 等模型的 <think>...</think> 推理块
    stripped = re.sub(r"<think>[\s\S]*?</think>", "", stripped)
    stripped = stripped.strip()

    # 提取 ```json ... ``` 或 ``` ... ``` 中的内容
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if code_block:
        candidate = code_block.group(1).strip()
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

    # 尝试直接解析
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    # 逐字符扫描，收集所有合法 JSON 对象，取最后一个
    candidates: list[dict[str, Any]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, character in enumerate(stripped):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = stripped[start : index + 1]
                try:
                    value = json.loads(candidate)
                    if isinstance(value, dict):
                        candidates.append(value)
                except json.JSONDecodeError:
                    pass
                start = -1

    if candidates:
        # 取 key 最多的 JSON 对象（通常是真正的输出，思考碎片 key 很少）
        candidates.sort(key=lambda d: len(d), reverse=True)
        return candidates[0]

    raise ValueError("LLM 回复中没有合法 JSON 对象")


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self._model_name = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout

    @property
    def model_name(self) -> str:
        return self._model_name

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_text = json.dumps(json_schema, ensure_ascii=False, separators=(",", ":"))
        request_prompt = (
            f"{user_prompt}\n\n必须严格满足以下 JSON Schema：\n{schema_text}"
        )
        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": settings.llm_max_tokens,
                "response_format": {
                    "type": "json_object",
                    "schema": json_schema,
                },
                "stream": False,
            },
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"LLM HTTP {response.status_code}: {response.text[:500]}"
            )
        body = response.json()
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(f"LLM 返回错误: {body['error']}")
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM 响应缺少 choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM 返回空内容")
        return extract_json_object(content)
