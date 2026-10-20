"""Operational records: audit events, background jobs, idempotency, provider checks and usage."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from copperbelt.db import Base
from copperbelt.models.base import IdMixin, StrEnum, TimestampMixin, utcnow


class AuditEvent(IdMixin, Base):
    """Append-only. UPDATE/DELETE are revoked from the application role in migrations."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_org_at", "org_id", "occurred_at"),)

    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("organisations.id"), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"  # will be retried after run_after
    dead = "dead"  # exhausted retries; needs operator action
    cancelled = "cancelled"


class Job(IdMixin, TimestampMixin, Base):
    """Durable job queue row.

    Jobs are inserted in the same transaction as the domain change that requires
    them (transactional outbox), and claimed with ``FOR UPDATE SKIP LOCKED``.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        UniqueConstraint("org_id", "dedupe_key", name="uq_jobs_dedupe"),
    )

    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("organisations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"), default=JobStatus.queued, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(String(300))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class IdempotencyRecord(IdMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("org_id", "key", "operation"),)

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organisations.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProviderCheck(IdMixin, Base):
    """Result of an actual connectivity/credential check against an external provider."""

    __tablename__ = "provider_checks"
    __table_args__ = (Index("ix_provider_checks_latest", "provider", "checked_at"),)

    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProviderUsage(IdMixin, Base):
    __tablename__ = "provider_usage"

    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("organisations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    units: Mapped[float] = mapped_column(Numeric(14, 4), default=0, nullable=False)
    unit_name: Mapped[str] = mapped_column(String(40), nullable=False)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(14, 4))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
