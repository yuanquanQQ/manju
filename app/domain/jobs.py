"""Pipeline 任务状态与转换规则。"""
from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    PAUSED = "PAUSED"
    CANCELED = "CANCELED"
    STALE = "STALE"


TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.CANCELED,
}


ALLOWED_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {
        JobStatus.RUNNING,
        JobStatus.PAUSED,
        JobStatus.CANCELED,
    },
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.RETRYING,
        JobStatus.PAUSED,
        JobStatus.CANCELED,
    },
    JobStatus.RETRYING: {
        JobStatus.RUNNING,
        JobStatus.FAILED,
        JobStatus.PAUSED,
        JobStatus.CANCELED,
    },
    JobStatus.PAUSED: {
        JobStatus.PENDING,
        JobStatus.CANCELED,
    },
    JobStatus.FAILED: {
        JobStatus.PENDING,
        JobStatus.CANCELED,
    },
    JobStatus.SUCCEEDED: {
        JobStatus.STALE,
    },
    JobStatus.STALE: {
        JobStatus.PENDING,
        JobStatus.CANCELED,
    },
    JobStatus.CANCELED: set(),
}


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in ALLOWED_JOB_TRANSITIONS[current]

