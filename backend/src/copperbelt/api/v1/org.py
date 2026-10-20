from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from copperbelt.api.schemas import JobOut, MemberIn, MemberOut, RoleChangeIn
from copperbelt.db import new_session
from copperbelt.errors import Conflict, Forbidden, NotFound, ValidationFailed
from copperbelt.jobs.queue import retry_job
from copperbelt.models.identity import Membership, Role, User
from copperbelt.models.ops import AuditEvent, Job
from copperbelt.security.auth import hash_password, validate_password_strength
from copperbelt.security.deps import Ctx, require
from copperbelt.services.audit import record_ctx

router = APIRouter(tags=["organisation"])


def _members(c: Ctx) -> list[MemberOut]:
    rows = c.db.execute(
        select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.org_id == c.org_id)
        .order_by(User.full_name)
    ).all()
    return [MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=str(m.role), is_active=m.is_active) for m, u in rows]


@router.get("/org/members", response_model=list[MemberOut])
def list_members(c: Ctx = Depends(require("projects:read"))) -> list[MemberOut]:
    return _members(c)


@router.post("/org/members", response_model=MemberOut, status_code=201)
def add_member(body: MemberIn, c: Ctx = Depends(require("members:manage"))) -> MemberOut:
    """Add a user to the organisation. Creates the account if it does not exist.
    (Email invitations need an email provider; until one is configured the admin sets an initial password.)"""
    if Role(body.role) == Role.admin and c.role != Role.owner:
        raise Forbidden("Only owners can add administrators")
    email = body.email.lower()
    user = c.db.scalar(select(User).where(User.email == email))
    if user is None:
        try:
            validate_password_strength(body.initial_password)
        except ValueError as exc:
            raise ValidationFailed(str(exc), code="weak_password") from exc
        user = User(email=email, full_name=body.full_name, password_hash=hash_password(body.initial_password))
        c.db.add(user)
        c.db.flush()
    existing = c.db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.org_id == c.org_id))
    if existing:
        raise Conflict("User is already a member")
    c.db.add(Membership(user_id=user.id, org_id=c.org_id, role=Role(body.role)))
    record_ctx(c, "org.member_added", "user", user.id, role=body.role)
    return MemberOut(user_id=user.id, email=user.email, full_name=user.full_name, role=body.role, is_active=True)


def _membership(c: Ctx, user_id: uuid.UUID) -> Membership:
    m = c.db.scalar(select(Membership).where(Membership.user_id == user_id, Membership.org_id == c.org_id))
    if m is None:
        raise NotFound("Member not found")
    if m.role == Role.owner and c.role != Role.owner:
        raise Forbidden("Only owners can change an owner's membership")
    return m


@router.patch("/org/members/{user_id}", response_model=list[MemberOut])
def change_role(user_id: uuid.UUID, body: RoleChangeIn, c: Ctx = Depends(require("members:manage"))) -> list[MemberOut]:
    m = _membership(c, user_id)
    if Role(body.role) == Role.admin and c.role != Role.owner:
        raise Forbidden("Only owners can grant the admin role")
    previous = m.role
    m.role = Role(body.role)
    record_ctx(c, "org.member_role_changed", "user", user_id, previous=str(previous), new=body.role)
    c.db.flush()
    return _members(c)


@router.delete("/org/members/{user_id}", status_code=204)
def revoke_member(user_id: uuid.UUID, c: Ctx = Depends(require("members:manage"))) -> None:
    """Revoke access immediately: membership is checked on every request."""
    if user_id == c.user.id:
        raise Conflict("You cannot revoke your own membership")
    m = _membership(c, user_id)
    if m.role == Role.owner:
        owners = c.db.scalar(select(func.count()).select_from(Membership).where(
            Membership.org_id == c.org_id, Membership.role == Role.owner, Membership.is_active.is_(True)))
        if owners <= 1:
            raise Conflict("Cannot remove the last owner")
    m.is_active = False
    record_ctx(c, "org.member_revoked", "user", user_id)


@router.get("/audit-events")
def audit_events(
    c: Ctx = Depends(require("audit:read")),
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    q = select(AuditEvent).where(AuditEvent.org_id == c.org_id)
    if entity_type:
        q = q.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditEvent.entity_id == entity_id)
    if action:
        q = q.where(AuditEvent.action == action)
    total = c.db.scalar(select(func.count()).select_from(q.subquery()))
    rows = c.db.scalars(q.order_by(AuditEvent.occurred_at.desc()).limit(limit).offset(offset)).all()
    return {
        "total": total, "limit": limit, "offset": offset,
        "items": [
            {"id": str(e.id), "action": e.action, "actor_id": str(e.actor_id) if e.actor_id else None,
             "entity_type": e.entity_type, "entity_id": e.entity_id, "detail": e.detail,
             "request_id": e.request_id, "occurred_at": e.occurred_at.isoformat()}
            for e in rows
        ],
    }


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(c: Ctx = Depends(require("documents:read")), status: str | None = None,
              limit: int = Query(50, ge=1, le=200)) -> list[Job]:
    q = select(Job).where(Job.org_id == c.org_id)
    if status:
        q = q.where(Job.status == status)
    return list(c.db.scalars(q.order_by(Job.created_at.desc()).limit(limit)))


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, c: Ctx = Depends(require("documents:read"))) -> Job:
    job = c.db.scalar(select(Job).where(Job.id == job_id, Job.org_id == c.org_id))
    if job is None:
        raise NotFound("Job not found")
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry(job_id: uuid.UUID, c: Ctx = Depends(require("jobs:manage"))) -> Job:
    job = get_job(job_id, c)
    retry_job(c.db, job)
    record_ctx(c, "job.retried", "job", job.id, kind=job.kind)
    return job


__all__ = ["router", "new_session"]
