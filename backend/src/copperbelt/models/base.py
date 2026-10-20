from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, onupdate=utcnow, nullable=False
    )


class TenantMixin:
    """Rows owned by one organisation. Protected by RLS (see migrations)."""

    @declared_attr
    def org_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(Uuid, ForeignKey("organisations.id", ondelete="RESTRICT"), index=True, nullable=False)


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:
        return self.value
