from __future__ import annotations

from typing import Any

import pytest

import app.adapters.llm as llm_module
from app.adapters.llm import OpenAICompatibleLLM


class _Response:
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"name":"张三","description":"英俊的青年剑客"}'
                    }
                }
            ]
        }


def test_complete_requests_schema_constrained_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(_url: str, *, json: dict[str, Any], timeout: int) -> _Response:
        captured["payload"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(llm_module.requests, "post", fake_post)
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["name", "description"],
    }

    result = OpenAICompatibleLLM(timeout=30).complete(
        system_prompt="只输出 JSON",
        user_prompt="描述人物",
        json_schema=schema,
    )

    assert result["name"] == "张三"
    assert captured["timeout"] == 30
    assert captured["payload"]["response_format"] == {
        "type": "json_object",
        "schema": schema,
    }

