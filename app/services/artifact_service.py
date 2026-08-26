"""项目产物登记与路径边界检查。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.files import sha256_file
from app.database.db import get_session
from app.database.models import Artifact, Job


def register_artifact(
    project_root: str | Path,
    file_path: str | Path,
    *,
    kind: str,
    job_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    root = Path(project_root).resolve()
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"产物文件不存在: {path}")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("产物必须位于项目目录内") from exc
    if not kind.strip():
        raise ValueError("产物 kind 不能为空")

    with get_session() as session, session.begin():
        if job_id and session.get(Job, job_id) is None:
            raise LookupError(f"任务不存在: {job_id}")
        existing = (
            session.query(Artifact)
            .filter(Artifact.relative_path == relative.as_posix())
            .one_or_none()
        )
        if existing is None:
            existing = Artifact(
                id=str(uuid4()),
                relative_path=relative.as_posix(),
                kind=kind.strip(),
                job_id=job_id,
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
                metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            )
            session.add(existing)
        else:
            existing.kind = kind.strip()
            existing.job_id = job_id
            existing.sha256 = sha256_file(path)
            existing.size_bytes = path.stat().st_size
            existing.metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    return existing

