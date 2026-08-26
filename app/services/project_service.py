"""项目创建、定位与元数据管理。"""
from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import yaml

from app.core.config import settings
from app.core.files import atomic_write_json, atomic_write_text
from app.database.db import migrate_database
from app.domain.projects import ProjectConfig, ProjectManifest

_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u3400-\u9fff]{1,64}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

PROJECT_SUBDIRECTORIES = (
    "novel/source",
    "novel/chapters",
    "novel/revisions",
    "database",
    "indexes",
    "assets/characters",
    "assets/locations",
    "assets/voices",
    "assets/props",
    "assets/styles",
    "production/episodes",
    "production/shots",
    "production/audio",
    "cache",
    "outputs",
    "outputs/episodes",
    "logs",
    # 兼容当前小说入库原型。
    "chapters",
)


def validate_project_name(name: str) -> str:
    value = name.strip()
    if not _PROJECT_NAME_RE.fullmatch(value):
        raise ValueError("项目名只能包含中英文、数字、下划线和连字符，长度 1–64")
    if value.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"项目名是 Windows 保留名称: {value}")
    return value


def resolve_project_dir(
    name: str,
    *,
    projects_dir: str | Path | None = None,
    must_exist: bool = False,
) -> Path:
    slug = validate_project_name(name)
    root = Path(projects_dir or settings.projects_dir).resolve()
    candidate = (root / slug).resolve()
    if candidate.parent != root:
        raise ValueError("项目路径超出 projects 目录")
    if must_exist and not candidate.is_dir():
        raise FileNotFoundError(f"项目不存在: {candidate}")
    return candidate


def _write_project_files(
    root: Path,
    manifest: ProjectManifest,
    config: ProjectConfig,
) -> None:
    for relative in PROJECT_SUBDIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    atomic_write_json(root / "project.json", manifest.model_dump(mode="json"))
    config_text = yaml.safe_dump(
        config.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=False,
    )
    atomic_write_text(root / "config.yaml", config_text)
    migrate_database(root / "database" / "world.db")


def create_project(
    name: str,
    *,
    display_name: str | None = None,
    projects_dir: str | Path | None = None,
) -> Path:
    slug = validate_project_name(name)
    projects_root = Path(projects_dir or settings.projects_dir).resolve()
    projects_root.mkdir(parents=True, exist_ok=True)
    target = resolve_project_dir(slug, projects_dir=projects_root)
    if target.exists():
        raise FileExistsError(f"项目已存在: {target}")

    staging = (projects_root / f".creating-{uuid4().hex}").resolve()
    if staging.parent != projects_root:
        raise ValueError("临时项目路径超出 projects 目录")
    staging.mkdir()
    try:
        manifest = ProjectManifest(
            slug=slug,
            display_name=(display_name or slug).strip() or slug,
        )
        _write_project_files(staging, manifest, ProjectConfig())
        staging.replace(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def load_project_manifest(root: str | Path) -> ProjectManifest:
    path = Path(root) / "project.json"
    return ProjectManifest.model_validate_json(path.read_text(encoding="utf-8"))


def ensure_legacy_project_metadata(root: str | Path, slug: str) -> ProjectManifest:
    """为旧项目补充清单与配置，不覆盖已有文件。"""
    project_root = Path(root)
    manifest_path = project_root / "project.json"
    if manifest_path.exists():
        return load_project_manifest(project_root)

    created_at = datetime.fromtimestamp(project_root.stat().st_ctime, tz=UTC)
    manifest = ProjectManifest(
        slug=validate_project_name(slug),
        display_name=slug,
        created_at=created_at,
        updated_at=created_at,
    )
    atomic_write_json(manifest_path, manifest.model_dump(mode="json"))

    config_path = project_root / "config.yaml"
    if not config_path.exists():
        config_text = yaml.safe_dump(
            ProjectConfig().model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        )
        atomic_write_text(config_path, config_text)
    return manifest


def load_project_config(root: str | Path) -> ProjectConfig:
    path = Path(root) / "config.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"项目配置必须是对象: {path}")
    return ProjectConfig.model_validate(value)
