"""持久化任务状态、转换、恢复与失效传播。"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.files import sha256_text
from app.database.db import get_session
from app.database.models import Job, JobDependency, SettingsSnapshot
from app.domain.jobs import JobStatus, can_transition


class JobNotFoundError(LookupError):
    pass


class InvalidJobTransitionError(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _load_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(f"任务不存在: {job_id}")
    return job


def create_job(
    job_type: str,
    *,
    payload: dict[str, Any] | None = None,
    input_hash: str = "",
    priority: int = 0,
    max_retries: int = 3,
    dependencies: list[str] | None = None,
    reuse_existing: bool = True,
) -> Job:
    if not job_type.strip():
        raise ValueError("job_type 不能为空")
    if max_retries < 0:
        raise ValueError("max_retries 不能小于 0")

    if input_hash and reuse_existing:
        reusable_statuses = [
            JobStatus.PENDING.value,
            JobStatus.RUNNING.value,
            JobStatus.RETRYING.value,
            JobStatus.PAUSED.value,
            JobStatus.SUCCEEDED.value,
        ]
        with get_session() as session:
            existing = session.scalar(
                select(Job)
                .where(
                    Job.job_type == job_type.strip(),
                    Job.input_hash == input_hash,
                    Job.status.in_(reusable_statuses),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            if existing is not None:
                return existing

    job = Job(
        id=str(uuid4()),
        job_type=job_type.strip(),
        status=JobStatus.PENDING.value,
        priority=priority,
        max_retries=max_retries,
        input_hash=input_hash,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    with get_session() as session, session.begin():
        session.add(job)
        for dependency_id in dependencies or []:
            _load_job(session, dependency_id)
            session.add(
                JobDependency(
                    job_id=job.id,
                    depends_on_job_id=dependency_id,
                )
            )
    return job


def get_job(job_id: str) -> Job:
    with get_session() as session:
        return _load_job(session, job_id)


def list_jobs(
    *,
    status: JobStatus | None = None,
    limit: int = 50,
) -> list[Job]:
    safe_limit = max(1, min(limit, 500))
    with get_session() as session:
        statement = select(Job).order_by(Job.created_at.desc()).limit(safe_limit)
        if status is not None:
            statement = statement.where(Job.status == status.value)
        return list(session.scalars(statement))


def transition_job(
    job_id: str,
    target: JobStatus,
    *,
    error_code: str = "",
    error_message: str = "",
    result: dict[str, Any] | None = None,
) -> Job:
    now = _utc_now()
    with get_session() as session, session.begin():
        job = _load_job(session, job_id)
        current = JobStatus(job.status)
        if not can_transition(current, target):
            raise InvalidJobTransitionError(
                f"任务不能从 {current.value} 转为 {target.value}: {job_id}"
            )

        job.status = target.value
        job.updated_at = now
        if target is JobStatus.RUNNING:
            job.started_at = job.started_at or now
            job.heartbeat_at = now
            job.attempt += 1
            job.pause_requested = False
            job.cancel_requested = False
        elif target is JobStatus.SUCCEEDED:
            job.progress = 1.0
            job.finished_at = now
            job.error_code = ""
            job.error_message = ""
            if result is not None:
                job.result_json = json.dumps(result, ensure_ascii=False)
        elif target in {JobStatus.FAILED, JobStatus.CANCELED}:
            job.finished_at = now
            if target is JobStatus.CANCELED:
                job.cancel_requested = False
                job.pause_requested = False
        elif target is JobStatus.PAUSED:
            job.pause_requested = False
        elif target is JobStatus.PENDING:
            job.finished_at = None
            job.pause_requested = False
            job.cancel_requested = False

        if error_code:
            job.error_code = error_code
        if error_message:
            job.error_message = error_message
        if result is not None and target is not JobStatus.SUCCEEDED:
            job.result_json = json.dumps(result, ensure_ascii=False)
    return job


def heartbeat_job(job_id: str, progress: float | None = None) -> Job:
    with get_session() as session, session.begin():
        job = _load_job(session, job_id)
        if JobStatus(job.status) not in {JobStatus.RUNNING, JobStatus.RETRYING}:
            raise InvalidJobTransitionError(
                f"只有运行中的任务能发送心跳: {job_id}"
            )
        job.heartbeat_at = _utc_now()
        if progress is not None:
            job.progress = min(1.0, max(0.0, progress))
    return job


def request_pause(job_id: str) -> Job:
    with get_session() as session, session.begin():
        job = _load_job(session, job_id)
        status = JobStatus(job.status)
        if status is JobStatus.PENDING:
            job.status = JobStatus.PAUSED.value
        elif status in {JobStatus.RUNNING, JobStatus.RETRYING}:
            job.pause_requested = True
        elif status is not JobStatus.PAUSED:
            raise InvalidJobTransitionError(f"当前状态不能暂停: {status.value}")
    return job


def request_cancel(job_id: str) -> Job:
    with get_session() as session, session.begin():
        job = _load_job(session, job_id)
        status = JobStatus(job.status)
        if status in {JobStatus.PENDING, JobStatus.PAUSED, JobStatus.FAILED, JobStatus.STALE}:
            job.status = JobStatus.CANCELED.value
            job.finished_at = _utc_now()
        elif status in {JobStatus.RUNNING, JobStatus.RETRYING}:
            job.cancel_requested = True
        elif status is not JobStatus.CANCELED:
            raise InvalidJobTransitionError(f"当前状态不能取消: {status.value}")
    return job


def resume_job(job_id: str) -> Job:
    with get_session() as session:
        job = _load_job(session, job_id)
        status = JobStatus(job.status)
        if status is JobStatus.FAILED and job.attempt > job.max_retries:
            raise InvalidJobTransitionError(
                f"任务已超过最大重试次数: {job.attempt}/{job.max_retries}"
            )
    return transition_job(job_id, JobStatus.PENDING)


def recover_interrupted_jobs(stale_after_seconds: int = 300) -> list[str]:
    """把失去心跳的运行任务转为 PAUSED，等待用户或调度器恢复。"""
    cutoff = _utc_now() - timedelta(seconds=max(1, stale_after_seconds))
    recovered: list[str] = []
    with get_session() as session, session.begin():
        statement = select(Job).where(
            Job.status.in_([JobStatus.RUNNING.value, JobStatus.RETRYING.value]),
            or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < cutoff),
        )
        for job in session.scalars(statement):
            job.status = JobStatus.PAUSED.value
            job.pause_requested = False
            job.error_code = "worker_lost"
            job.error_message = "任务进程失去心跳，已自动暂停"
            recovered.append(job.id)
    return recovered


def mark_stale_with_dependents(job_id: str) -> list[str]:
    """将成功任务及其成功下游标为 STALE。"""
    changed: list[str] = []
    queue = [job_id]
    visited: set[str] = set()
    with get_session() as session, session.begin():
        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            job = _load_job(session, current_id)
            if JobStatus(job.status) is JobStatus.SUCCEEDED:
                job.status = JobStatus.STALE.value
                changed.append(job.id)
            downstream = session.scalars(
                select(JobDependency.job_id).where(
                    JobDependency.depends_on_job_id == current_id
                )
            )
            queue.extend(downstream)
    return changed


def create_settings_snapshot(
    settings_value: dict[str, Any],
    *,
    job_id: str | None = None,
) -> SettingsSnapshot:
    serialized = json.dumps(
        settings_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot = SettingsSnapshot(
        id=str(uuid4()),
        job_id=job_id,
        settings_json=serialized,
        settings_hash=sha256_text(serialized),
    )
    with get_session() as session, session.begin():
        if job_id:
            _load_job(session, job_id)
        session.add(snapshot)
    return snapshot
