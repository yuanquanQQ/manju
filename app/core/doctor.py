"""本地运行环境诊断。"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import requests

from app.core.config import settings

DiagnosticStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    status: DiagnosticStatus
    message: str


def _python_check() -> DiagnosticCheck:
    version = sys.version_info
    supported = (3, 11) <= version[:2] < (3, 13)
    status: DiagnosticStatus = "PASS" if supported else "FAIL"
    return DiagnosticCheck(
        "Python",
        status,
        f"{version.major}.{version.minor}.{version.micro}（要求 3.11 或 3.12）",
    )


def _command_check(command: str, *, required: bool = True) -> DiagnosticCheck:
    path = shutil.which(command)
    if path:
        return DiagnosticCheck(command, "PASS", path)
    return DiagnosticCheck(
        command,
        "FAIL" if required else "WARN",
        "未找到可执行文件",
    )


def _gpu_check() -> DiagnosticCheck:
    command = shutil.which("nvidia-smi")
    if not command:
        return DiagnosticCheck("本机 NVIDIA GPU", "WARN", "未找到 nvidia-smi")
    try:
        result = subprocess.run(
            [
                command,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        message = result.stdout.strip().replace("\n", "; ")
        return DiagnosticCheck("本机 NVIDIA GPU", "PASS", message or "可用")
    except (OSError, subprocess.SubprocessError) as exc:
        return DiagnosticCheck("本机 NVIDIA GPU", "WARN", f"查询失败: {exc}")


def _llm_check() -> DiagnosticCheck:
    url = f"{settings.llm_base_url.rstrip('/')}/v1/models"
    try:
        response = requests.get(url, timeout=3)
        if response.status_code >= 400:
            return DiagnosticCheck(
                "LLM 服务",
                "WARN",
                f"HTTP {response.status_code}: {url}",
            )
        return DiagnosticCheck("LLM 服务", "PASS", url)
    except requests.RequestException as exc:
        return DiagnosticCheck("LLM 服务", "WARN", f"暂不可连接: {exc}")


def _storage_check() -> DiagnosticCheck:
    target = settings.project_root
    usage = shutil.disk_usage(target)
    free_gb = usage.free / (1024**3)
    status: DiagnosticStatus = "PASS" if free_gb >= 20 else "WARN"
    return DiagnosticCheck("磁盘空间", status, f"可用 {free_gb:.1f} GB: {target}")


def _database_check(project_root: Path) -> DiagnosticCheck:
    path = project_root / "database" / "world.db"
    if not path.is_file():
        return DiagnosticCheck("项目数据库", "FAIL", f"不存在: {path}")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()
        finally:
            connection.close()
        if integrity != "ok":
            return DiagnosticCheck("项目数据库", "FAIL", f"完整性: {integrity}")
        version = row[0] if row else "未登记"
        return DiagnosticCheck("项目数据库", "PASS", f"完整，schema v{version}")
    except sqlite3.Error as exc:
        return DiagnosticCheck("项目数据库", "FAIL", str(exc))


def run_diagnostics(
    *,
    project_root: str | Path | None = None,
    check_llm: bool = True,
) -> list[DiagnosticCheck]:
    checks = [
        _python_check(),
        DiagnosticCheck(
            "SQLite",
            "PASS",
            sqlite3.sqlite_version,
        ),
        _command_check("ffmpeg"),
        _command_check("ffprobe"),
        _gpu_check(),
        _storage_check(),
    ]
    if check_llm:
        checks.append(_llm_check())
    if project_root is not None:
        checks.append(_database_check(Path(project_root)))
    return checks
