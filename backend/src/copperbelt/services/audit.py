from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from copperbelt.models.ops import AuditEvent


def record(
    db: Session,
    action: str,
    *,
    org_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    entity_type: str | None = None,
    entity_id: Any = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip: str | None = None,
) -> None:
    """Write an audit event in the caller's transaction so it commits or rolls back with the change."""
    db.add(
        AuditEvent(
            org_id=org_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            detail=detail or {},
            request_id=request_id,
            ip_address=ip,
        )
    )


def record_ctx(c, action: str, entity_type: str | None = None, entity_id: Any = None, **detail: Any) -> None:  # noqa: ANN001
    record(
        c.db,
        action,
        org_id=c.org_id,
        actor_id=c.user.id,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
        request_id=c.request_id,
        ip=c.ip,
    )
