"""Request context: authenticated user, active organisation, role and tenant-bound session."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from copperbelt.db import new_session
from copperbelt.errors import Forbidden, Unauthorized
from copperbelt.models.identity import Membership, Role, User
from copperbelt.security.auth import TokenError, decode_token
from copperbelt.security.permissions import has_permission

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Ctx:
    user: User
    org_id: uuid.UUID
    role: Role
    db: Session
    request_id: str | None
    ip: str | None

    def can(self, permission: str) -> bool:
        return self.user.is_platform_admin or has_permission(self.role, permission)

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise Forbidden(f"Your role ({self.role}) lacks permission '{permission}'")


def _load_user(token: str | None) -> User:
    if not token:
        raise Unauthorized("Authentication required")
    try:
        payload = decode_token(token, "access")
    except TokenError as exc:
        raise Unauthorized(str(exc)) from exc
    session = new_session(system=True)
    try:
        user = session.get(User, uuid.UUID(payload["sub"]))
        if user is None or not user.is_active or user.token_version != payload["tv"]:
            raise Unauthorized("Session is no longer valid")
        session.expunge(user)
        return user
    finally:
        session.close()


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:
    return _load_user(creds.credentials if creds else None)


def ctx(
    request: Request,
    user: User = Depends(current_user),
    x_org_id: uuid.UUID | None = Header(default=None, alias="X-Org-Id"),
) -> Iterator[Ctx]:
    if x_org_id is None:
        raise Forbidden("X-Org-Id header is required for this endpoint", code="org_required")
    lookup = new_session(system=True)
    try:
        membership = lookup.scalar(
            select(Membership).where(Membership.user_id == user.id, Membership.org_id == x_org_id, Membership.is_active.is_(True))
        )
        if membership is None or not membership.organisation.is_active:
            # Do not reveal whether the organisation exists.
            raise Forbidden("You are not a member of this organisation", code="not_a_member")
        role = membership.role
    finally:
        lookup.close()

    db = new_session(x_org_id)
    context = Ctx(
        user=user,
        org_id=x_org_id,
        role=role,
        db=db,
        request_id=getattr(request.state, "request_id", None),
        ip=request.client.host if request.client else None,
    )
    try:
        yield context
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def require(permission: str):  # noqa: ANN201
    """Permission-checked request context.

    scope="function" makes the transaction commit BEFORE the response is sent, so a client that
    receives 2xx can rely on the change being durable and visible to its next request.
    """

    def _dep(c: Ctx = Depends(ctx, scope="function")) -> Ctx:
        c.require(permission)
        return c

    return _dep
