"""统一日志：loguru 单实例。"""
from __future__ import annotations

import sys

from loguru import logger

from app.core.config import settings


def _stderr_sink(message) -> None:
    """每次写入时解析当前 stderr，兼容 CLI 测试的临时捕获流。"""
    sys.stderr.write(str(message))


def setup_logger() -> None:
    """初始化日志：控制台 + 文件。"""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.configure(extra={"project_id": "-", "job_id": "-"})
    logger.add(
        _stderr_sink,
        level="INFO",
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
            "project={extra[project_id]} job={extra[job_id]} | {message}"
        ),
    )
    logger.add(
        settings.logs_dir / "app.log",
        level="DEBUG",
        rotation="20 MB",
        retention="10 days",
        encoding="utf-8",
        enqueue=True,
    )


def bind_logger(*, project_id: str = "-", job_id: str = "-"):
    """返回带项目/任务上下文的 logger。"""
    return logger.bind(project_id=project_id, job_id=job_id)


__all__ = ["bind_logger", "logger", "setup_logger"]
