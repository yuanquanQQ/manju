from __future__ import annotations

from pathlib import Path

import pytest

import app.services.local_llm_service as llm_module
from app.services.local_llm_service import LocalLlmService


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"data": [{"id": "qwen/qwen3.5-9b"}]}


def test_llm_status_reports_online_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"partial")
    service = LocalLlmService(model)
    monkeypatch.setattr(llm_module.requests, "get", lambda *_args, **_kwargs: _Response())

    status = service.check_status()

    assert status.online is True
    assert status.model_ids == ["qwen/qwen3.5-9b"]
    assert "已就绪" in status.message


def test_llm_status_explains_incomplete_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"partial")
    service = LocalLlmService(model)

    def offline(*_args, **_kwargs):
        raise llm_module.requests.ConnectionError("offline")

    monkeypatch.setattr(llm_module.requests, "get", offline)
    status = service.check_status()

    assert status.online is False
    assert status.model_ready is False
    assert "文件不完整" in status.message


def test_llm_start_rejects_missing_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = LocalLlmService(tmp_path / "missing.gguf")

    def offline(*_args, **_kwargs):
        raise llm_module.requests.ConnectionError("offline")

    monkeypatch.setattr(llm_module.requests, "get", offline)
    with pytest.raises(FileNotFoundError, match="尚未下载"):
        service.start()
