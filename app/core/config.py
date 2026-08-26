"""全局配置加载。

优先级：环境变量 > 项目根目录 .env > 代码默认值。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_dotenv(env_path: Path) -> None:
    """极简 .env 解析器（不引入 python-dotenv 依赖）。

    支持 KEY=VALUE；忽略空行和以 # 开头的注释。
    不会覆盖已经设置过的环境变量。
    """
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# 进程启动即加载一次
_load_dotenv(_project_root() / ".env")


@dataclass(frozen=True)
class Settings:
    # 路径
    project_root: Path = field(default_factory=_project_root)
    projects_dir: Path = field(default_factory=lambda: _project_root() / "projects")
    models_dir: Path = field(default_factory=lambda: _project_root() / "models")
    workflows_dir: Path = field(default_factory=lambda: _project_root() / "workflows")
    logs_dir: Path = field(default_factory=lambda: _project_root() / "logs")

    # 本地 LLM（OpenAI 兼容协议：LM Studio / Ollama / vLLM 等）
    llm_base_url: str = "http://localhost:1234"
    llm_model: str = "qwen/qwen3.5-9b"
    llm_timeout: int = 600
    llm_max_retries: int = 3
    llm_max_tokens: int = 4096
    llm_model_path: Path = field(
        default_factory=lambda: _project_root()
        / "models"
        / "llm"
        / "Qwen.Qwen3.5-9B.Q4_K_M.gguf"
    )
    llm_context_size: int = 8192

    # 抽取参数
    extract_max_chars: int = 6000
    extract_concurrency: int = 1

    # ComfyUI
    comfyui_url: str = "localhost:8189"
    comfyui_timeout: int = 600

    # 数据库与 Pipeline
    sqlite_busy_timeout_ms: int = 30000
    pipeline_stale_after_seconds: int = 300

    @property
    def ollama_url(self) -> str:
        """兼容旧调用；新代码使用 llm_base_url。"""
        return self.llm_base_url

    @property
    def ollama_model(self) -> str:
        """兼容旧调用；新代码使用 llm_model。"""
        return self.llm_model


def load_settings() -> Settings:
    """兼容新旧环境变量名（OLLAMA_* 旧名仍然能识别）。"""
    url = os.getenv("LLM_BASE_URL") or os.getenv("OLLAMA_URL", "http://localhost:1234")
    model = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL", "qwen/qwen3.5-9b")
    return Settings(
        llm_base_url=url,
        llm_model=model,
        llm_timeout=int(os.getenv("LLM_TIMEOUT") or os.getenv("OLLAMA_TIMEOUT", "600")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES") or os.getenv("OLLAMA_MAX_RETRIES", "3")),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
        llm_model_path=Path(
            os.getenv(
                "LLM_MODEL_PATH",
                str(
                    _project_root()
                    / "models"
                    / "llm"
                    / "Qwen.Qwen3.5-9B.Q4_K_M.gguf"
                ),
            )
        ),
        llm_context_size=int(os.getenv("LLM_CONTEXT_SIZE", "8192")),
        extract_max_chars=int(os.getenv("EXTRACT_MAX_CHARS", "6000")),
        extract_concurrency=int(os.getenv("EXTRACT_CONCURRENCY", "1")),
        comfyui_url=os.getenv("COMFYUI_URL", "localhost:8189"),
        comfyui_timeout=int(os.getenv("COMFYUI_TIMEOUT", "600")),
        sqlite_busy_timeout_ms=int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "30000")),
        pipeline_stale_after_seconds=int(
            os.getenv("PIPELINE_STALE_AFTER_SECONDS", "300")
        ),
    )


settings: Settings = load_settings()
