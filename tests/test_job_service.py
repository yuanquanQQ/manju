from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.files import atomic_write_text
from app.database.db import get_session, init_db
from app.database.models import Artifact, Job
from app.domain.jobs import JobStatus
from app.services.artifact_service import register_artifact
from app.services.job_service import (
    InvalidJobTransitionError,
    create_job,
    heartbeat_job,
    list_jobs,
    mark_stale_with_dependents,
    recover_interrupted_jobs,
    request_cancel,
    request_pause,
    resume_job,
    transition_job,
)


@pytest.fixture()
def project_root(tmp_path):
    root = tmp_path / "project"
    (root / "database").mkdir(parents=True)
    init_db(root / "database" / "world.db")
    return root


def test_job_lifecycle(project_root):
    job = create_job("demo", payload={"chapter": 1})
    assert job.status == JobStatus.PENDING.value

    transition_job(job.id, JobStatus.RUNNING)
    heartbeat_job(job.id, 0.5)
    paused_request = request_pause(job.id)
    assert paused_request.pause_requested is True

    transition_job(job.id, JobStatus.PAUSED)
    resumed = resume_job(job.id)
    assert resumed.status == JobStatus.PENDING.value

    canceled = request_cancel(job.id)
    assert canceled.status == JobStatus.CANCELED.value
    with pytest.raises(InvalidJobTransitionError):
        resume_job(job.id)


def test_job_with_same_input_hash_is_reused(project_root):
    first = create_job("compile", input_hash="sha256:same")
    second = create_job("compile", input_hash="sha256:same")
    forced = create_job(
        "compile",
        input_hash="sha256:same",
        reuse_existing=False,
    )

    assert second.id == first.id
    assert forced.id != first.id


def test_recover_interrupted_job(project_root):
    job = create_job("demo")
    transition_job(job.id, JobStatus.RUNNING)
    with get_session() as session, session.begin():
        stored = session.get(Job, job.id)
        stored.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)

    recovered = recover_interrupted_jobs(stale_after_seconds=60)
    assert recovered == [job.id]
    stored = list_jobs(limit=1)[0]
    assert stored.status == JobStatus.PAUSED.value
    assert stored.error_code == "worker_lost"


def test_stale_propagates_to_successful_dependents(project_root):
    upstream = create_job("compile")
    downstream = create_job("render", dependencies=[upstream.id])
    transition_job(upstream.id, JobStatus.RUNNING)
    transition_job(upstream.id, JobStatus.SUCCEEDED)
    transition_job(downstream.id, JobStatus.RUNNING)
    transition_job(downstream.id, JobStatus.SUCCEEDED)

    changed = mark_stale_with_dependents(upstream.id)
    assert set(changed) == {upstream.id, downstream.id}


def test_artifact_must_be_inside_project(project_root, tmp_path):
    output = project_root / "outputs" / "demo.txt"
    atomic_write_text(output, "ok")
    artifact = register_artifact(project_root, output, kind="test")
    assert artifact.relative_path == "outputs/demo.txt"

    with get_session() as session:
        assert session.get(Artifact, artifact.id) is not None

    outside = tmp_path / "outside.txt"
    atomic_write_text(outside, "no")
    with pytest.raises(ValueError):
        register_artifact(project_root, outside, kind="test")
