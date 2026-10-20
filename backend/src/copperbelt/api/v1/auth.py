from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select

from copperbelt.api.ratelimit import limiter
from copperbelt.api.schemas import LoginIn, MeOut, MembershipOut, RegisterIn, TokenOut
from copperbelt.config import get_settings
from copperbelt.db import system_session
from copperbelt.errors import Conflict, Unauthorized, ValidationFailed
from copperbelt.models.identity import Membership, Organisation, Role, User
from copperbelt.security.auth import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_dummy,
    verify_password,
)
from copperbelt.security.deps import current_user
from copperbelt.services.audit import record

router = APIRouter(prefix="/auth", tags=["auth"])
REFRESH_COOKIE = "cbi_refresh"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _issue(response: Response, user: User) -> TokenOut:
    s = get_settings()
    response.set_cookie(
        REFRESH_COOKIE, create_token(user.id, user.token_version, "refresh"),
        max_age=s.refresh_token_days * 86400, httponly=True, samesite="strict",
        secure=s.environment in ("staging", "production"), path="/api/v1/auth",
    )
    return TokenOut(access_token=create_token(user.id, user.token_version, "access"), expires_in=s.access_token_minutes * 60)


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{base}-{uuid.uuid4().hex[:6]}"


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, request: Request, response: Response) -> TokenOut:
    """Create a user and a new organisation they own."""
    limiter.hit(f"register:{_client_ip(request)}", get_settings().login_rate_limit_per_minute)
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="weak_password") from exc
    with system_session() as db:
        if db.scalar(select(User).where(User.email == body.email.lower())):
            raise Conflict("An account with this email already exists", code="email_taken")
        user = User(email=body.email.lower(), full_name=body.full_name, password_hash=hash_password(body.password))
        org = Organisation(name=body.organisation_name, slug=_slug(body.organisation_name))
        db.add_all([user, org])
        db.flush()
        db.add(Membership(user_id=user.id, org_id=org.id, role=Role.owner))
        record(db, "auth.registered", org_id=org.id, actor_id=user.id, entity_type="organisation", entity_id=org.id,
               request_id=getattr(request.state, "request_id", None), ip=_client_ip(request))
        db.flush()
        return _issue(response, user)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response) -> TokenOut:
    ip = _client_ip(request)
    s = get_settings()
    limiter.hit(f"login-ip:{ip}", s.login_rate_limit_per_minute)
    limiter.hit(f"login-email:{body.email.lower()}", s.login_rate_limit_per_minute)
    with system_session() as db:
        user = db.scalar(select(User).where(User.email == body.email.lower()))
        if user is None:
            verify_dummy()
            ok = False
        else:
            ok = user.is_active and verify_password(user.password_hash, body.password)
        record(db, "auth.login_succeeded" if ok else "auth.login_failed", org_id=None,
               actor_id=user.id if user else None, detail={"email": body.email.lower()} if not ok else {},
               request_id=getattr(request.state, "request_id", None), ip=ip)
        if not ok:
            db.commit()
            raise Unauthorized("Invalid email or password", code="invalid_credentials")
        user.last_login_at = datetime.now(UTC)
        return _issue(response, user)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, x_requested_with: str | None = Header(default=None)) -> TokenOut:
    # The custom header cannot be sent cross-site without a CORS preflight: CSRF defence for the cookie.
    if x_requested_with != "copperbelt":
        raise Unauthorized("Missing X-Requested-With header", code="csrf_check_failed")
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise Unauthorized("No refresh session")
    try:
        payload = decode_token(token, "refresh")
    except TokenError as exc:
        raise Unauthorized(str(exc)) from exc
    with system_session() as db:
        user = db.get(User, uuid.UUID(payload["sub"]))
        if user is None or not user.is_active or user.token_version != payload["tv"]:
            raise Unauthorized("Session revoked")
        return _issue(response, user)


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, response: Response, user: User = Depends(current_user)) -> Response:
    """Revoke every access and refresh token issued to the caller."""
    with system_session() as db:
        u = db.get(User, user.id)
        u.token_version += 1
        record(db, "auth.sessions_revoked", org_id=None, actor_id=user.id,
               request_id=getattr(request.state, "request_id", None), ip=_client_ip(request))
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)) -> MeOut:
    with system_session() as db:
        rows = db.scalars(select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))).all()
        return MeOut(
            id=user.id, email=user.email, full_name=user.full_name,
            memberships=[MembershipOut(org_id=m.org_id, org_name=m.organisation.name, role=str(m.role)) for m in rows],
        )
