from __future__ import annotations

from app.core.config import load_settings


def test_new_llm_environment_names_take_priority(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("LLM_MODEL", "new-model")
    monkeypatch.setenv("OLLAMA_URL", "http://old:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "old-model")

    value = load_settings()
    assert value.llm_base_url == "http://127.0.0.1:9999"
    assert value.llm_model == "new-model"
    assert value.llm_context_size == 8192
    assert value.llm_model_path.name == "Qwen.Qwen3.5-9B.Q4_K_M.gguf"
    assert value.ollama_url == value.llm_base_url
    assert value.ollama_model == value.llm_model
