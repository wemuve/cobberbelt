"""PostgreSQL-backed durable job queue.

Why not Redis/Celery at this stage: jobs are created in the same transaction as
the domain change (a transactional outbox, so no lost or phantom jobs), and
claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` which is safe with many
workers. See docs/adr/0003-job-queue.md.
"""

from __future__ import annotations

import socket
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from copperbelt.config import get_settings
from copperbelt.db import new_session, system_session
from copperbelt.models.ops import Job, JobStatus

log = structlog.get_logger()

Handler = Callable[["JobContext"], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}


class PermanentJobError(Exception):
    """Raise from a handler when retrying cannot succeed (e.g. corrupt file)."""


def handler(kind: str) -> Callable[[Handler], Handler]:
    def _register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return _register


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    org_id: uuid.UUID | None,
    created_by: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> Job:
    """Add a job inside the caller's transaction. With a dedupe_key, an active duplicate is returned instead."""
    if dedupe_key:
        existing = db.scalar(
            select(Job).where(
                Job.org_id == org_id,
                Job.dedupe_key == dedupe_key,
                Job.status.in_([JobStatus.queued, JobStatus.running, JobStatus.failed]),
            )
        )
        if existing:
            return existing
        # Free the key held by a finished job so the work can be requested again.
        db.execute(update(Job).where(Job.org_id == org_id, Job.dedupe_key == dedupe_key).values(dedupe_key=None))
    job = Job(
        kind=kind,
        payload=payload,
        org_id=org_id,
        created_by=created_by,
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    db.add(job)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(Job).where(Job.org_id == org_id, Job.dedupe_key == dedupe_key))
        if existing is None:
            raise
        return existing
    return job


class JobContext:
    def __init__(self, job: Job) -> None:
        self.job_id = job.id
        self.kind = job.kind
        self.payload = dict(job.payload)
        self.org_id = job.org_id
        self.attempt = job.attempts
        self.max_attempts = job.max_attempts

    def session(self) -> Session:
        """A session bound to the job's tenant (RLS applies)."""
        return new_session(self.org_id) if self.org_id else new_session(system=True)

    def progress(self, fraction: float, message: str | None = None) -> None:
        with system_session() as s:
            s.execute(
                update(Job)
                .where(Job.id == self.job_id)
                .values(progress=round(max(0.0, min(1.0, fraction)) * 100, 2), progress_message=message)
            )


def _backoff(attempt: int) -> timedelta:
    return timedelta(seconds=min(3600, 10 * (2 ** max(0, attempt - 1))))


def claim_next(worker_id: str) -> Job | None:
    settings = get_settings()
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=settings.job_lock_timeout_seconds)
    with system_session() as s:
        job = s.scalar(
            select(Job)
            .where(
                or_(
                    Job.status.in_([JobStatus.queued, JobStatus.failed]) & (Job.run_after <= now),
                    # Recover jobs whose worker died mid-run.
                    (Job.status == JobStatus.running) & (Job.locked_at < stale),
                )
            )
            .order_by(Job.run_after)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = JobStatus.running
        job.attempts += 1
        job.locked_by = worker_id
        job.locked_at = now
        s.flush()
        s.expunge(job)
        return job


def run_one(worker_id: str | None = None) -> Job | None:
    """Claim and execute a single job. Returns the job (with final state) or None if the queue is empty."""
    worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"
    job = claim_next(worker_id)
    if job is None:
        return None
    fn = HANDLERS.get(job.kind)
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    run_after = None
    try:
        if fn is None:
            raise PermanentJobError(f"No handler registered for job kind '{job.kind}'")
        result = fn(JobContext(job)) or {}
        status = JobStatus.succeeded
    except PermanentJobError as exc:
        status, error = JobStatus.dead, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}"
        if job.attempts >= job.max_attempts:
            status = JobStatus.dead
        else:
            status, run_after = JobStatus.failed, datetime.now(UTC) + _backoff(job.attempts)
    log.info("job.finished", job_id=str(job.id), kind=job.kind, status=str(status), attempt=job.attempts)
    with system_session() as s:
        values: dict[str, Any] = {"status": status, "locked_by": None, "locked_at": None, "last_error": error}
        if status == JobStatus.succeeded:
            values.update(result=result, progress=100, finished_at=datetime.now(UTC))
        elif status == JobStatus.dead:
            values.update(finished_at=datetime.now(UTC))
        if run_after:
            values["run_after"] = run_after
        s.execute(update(Job).where(Job.id == job.id).values(**values))
        if fn is not None and status == JobStatus.dead and hasattr(fn, "on_dead"):
            fn.on_dead(JobContext(job), error)  # type: ignore[attr-defined]
    job.status, job.result, job.last_error = status, result, error
    return job


def drain(max_jobs: int = 100) -> int:
    """Run queued jobs until empty (used by tests and the CLI)."""
    n = 0
    while n < max_jobs and run_one("drain") is not None:
        n += 1
    return n


def retry_job(db: Session, job: Job) -> Job:
    if job.status not in (JobStatus.dead, JobStatus.failed, JobStatus.cancelled):
        from copperbelt.errors import Conflict

        raise Conflict(f"Only failed, dead or cancelled jobs can be retried (status is {job.status})")
    job.status = JobStatus.queued
    job.attempts = 0
    job.run_after = datetime.now(UTC)
    job.last_error = None
    job.finished_at = None
    return job
