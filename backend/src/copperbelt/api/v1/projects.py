from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from copperbelt.api.schemas import (
    AssessmentIn,
    AssessmentOut,
    AssessmentStatusIn,
    GapIn,
    GapOut,
    LinkIdsIn,
    ProjectIn,
    ProjectMemberIn,
    ProjectOut,
    RiskIn,
    RiskOut,
)
from copperbelt.errors import NotFound, ValidationFailed
from copperbelt.models.evidence import Claim
from copperbelt.models.identity import Membership
from copperbelt.models.projects import (
    Assessment,
    EvidenceGap,
    Project,
    ProjectDocument,
    ProjectFeature,
    ProjectMember,
    ProjectRole,
    Risk,
    RiskStatus,
)
from copperbelt.security.deps import Ctx, require
from copperbelt.services import projects as svc
from copperbelt.services.access import get_visible_document
from copperbelt.services.audit import record_ctx
from copperbelt.services.evidence import get_claim, get_feature

router = APIRouter(prefix="/projects", tags=["projects"])


def _out(c: Ctx, p: Project) -> dict:
    role = svc.project_role(c, p.id)
    return {**ProjectOut.model_validate(p).model_dump(), "my_role": str(role) if role else ("admin" if c.can("members:manage") else None)}


@router.get("", response_model=list[ProjectOut])
def list_projects(c: Ctx = Depends(require("projects:read"))) -> list[dict]:
    return [_out(c, p) for p in c.db.scalars(svc.visible_projects_query(c).order_by(Project.created_at.desc()))]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, c: Ctx = Depends(require("projects:write"))) -> dict:
    return _out(c, svc.create_project(c, body))


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, c: Ctx = Depends(require("projects:read"))) -> dict:
    return _out(c, svc.get_project(c, project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: uuid.UUID, body: ProjectIn, c: Ctx = Depends(require("projects:write"))) -> dict:
    p = svc.get_project(c, project_id, write=True)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    record_ctx(c, "project.updated", "project", p.id)
    return _out(c, p)


@router.post("/{project_id}/members", status_code=204)
def add_project_member(project_id: uuid.UUID, body: ProjectMemberIn, c: Ctx = Depends(require("projects:write"))) -> None:
    p = svc.get_project(c, project_id, write=True)
    if not c.db.scalar(
        select(Membership).where(Membership.user_id == body.user_id, Membership.org_id == c.org_id, Membership.is_active.is_(True))
    ):
        raise NotFound("User is not a member of this organisation")
    m = c.db.scalar(select(ProjectMember).where(ProjectMember.project_id == p.id, ProjectMember.user_id == body.user_id))
    if m:
        m.role = ProjectRole(body.role)
    else:
        c.db.add(ProjectMember(org_id=c.org_id, project_id=p.id, user_id=body.user_id, role=ProjectRole(body.role)))
    record_ctx(c, "project.member_set", "project", p.id, user_id=str(body.user_id), role=body.role)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
def remove_project_member(project_id: uuid.UUID, user_id: uuid.UUID, c: Ctx = Depends(require("projects:write"))) -> None:
    p = svc.get_project(c, project_id, write=True)
    m = c.db.scalar(select(ProjectMember).where(ProjectMember.project_id == p.id, ProjectMember.user_id == user_id))
    if m:
        c.db.delete(m)
    record_ctx(c, "project.member_removed", "project", p.id, user_id=str(user_id))


@router.post("/{project_id}/documents", status_code=204)
def link_documents(project_id: uuid.UUID, body: LinkIdsIn, c: Ctx = Depends(require("projects:write"))) -> None:
    p = svc.get_project(c, project_id, write=True)
    for did in body.ids:
        get_visible_document(c, did)
        if not c.db.scalar(select(ProjectDocument).where(ProjectDocument.project_id == p.id, ProjectDocument.document_id == did)):
            c.db.add(ProjectDocument(org_id=c.org_id, project_id=p.id, document_id=did))
    record_ctx(c, "project.documents_linked", "project", p.id, document_ids=[str(i) for i in body.ids])


@router.post("/{project_id}/features", status_code=204)
def link_features(project_id: uuid.UUID, body: LinkIdsIn, c: Ctx = Depends(require("projects:write"))) -> None:
    p = svc.get_project(c, project_id, write=True)
    for fid in body.ids:
        get_feature(c, fid)
        if not c.db.scalar(select(ProjectFeature).where(ProjectFeature.project_id == p.id, ProjectFeature.feature_id == fid)):
            c.db.add(ProjectFeature(org_id=c.org_id, project_id=p.id, feature_id=fid))
    record_ctx(c, "project.features_linked", "project", p.id, feature_ids=[str(i) for i in body.ids])


@router.get("/{project_id}/dossier")
def get_dossier(project_id: uuid.UUID, c: Ctx = Depends(require("projects:read"))) -> dict:
    return svc.dossier(c, svc.get_project(c, project_id))


# ------------------------------------------------------------------ risks & gaps


def _check_claims(c: Ctx, ids: list[uuid.UUID]) -> None:
    for cid in ids:
        get_claim(c, cid)


@router.get("/{project_id}/risks", response_model=list[RiskOut])
def list_risks(project_id: uuid.UUID, c: Ctx = Depends(require("projects:read"))) -> list[Risk]:
    p = svc.get_project(c, project_id)
    return list(c.db.scalars(select(Risk).where(Risk.project_id == p.id).order_by(Risk.created_at)))


@router.post("/{project_id}/risks", response_model=RiskOut, status_code=201)
def create_risk(project_id: uuid.UUID, body: RiskIn, c: Ctx = Depends(require("projects:write"))) -> Risk:
    p = svc.get_project(c, project_id, write=True)
    _check_claims(c, body.claim_ids)
    if not body.claim_ids and not body.verification_action:
        raise ValidationFailed("A risk needs supporting claims or a verification action that would resolve it")
    data = body.model_dump()
    data["status"] = RiskStatus(data["status"])
    r = Risk(org_id=c.org_id, project_id=p.id, created_by=c.user.id, **data)
    c.db.add(r)
    c.db.flush()
    record_ctx(c, "risk.created", "risk", r.id, project_id=str(p.id))
    return r


@router.put("/{project_id}/risks/{risk_id}", response_model=RiskOut)
def update_risk(project_id: uuid.UUID, risk_id: uuid.UUID, body: RiskIn, c: Ctx = Depends(require("projects:write"))) -> Risk:
    p = svc.get_project(c, project_id, write=True)
    r = c.db.scalar(select(Risk).where(Risk.id == risk_id, Risk.project_id == p.id))
    if r is None:
        raise NotFound("Risk not found")
    _check_claims(c, body.claim_ids)
    for k, v in body.model_dump().items():
        setattr(r, k, RiskStatus(v) if k == "status" else v)
    record_ctx(c, "risk.updated", "risk", r.id)
    return r


@router.get("/{project_id}/gaps", response_model=list[GapOut])
def list_gaps(project_id: uuid.UUID, c: Ctx = Depends(require("projects:read"))) -> list[EvidenceGap]:
    p = svc.get_project(c, project_id)
    return list(c.db.scalars(select(EvidenceGap).where(EvidenceGap.project_id == p.id).order_by(EvidenceGap.created_at)))


@router.post("/{project_id}/gaps", response_model=GapOut, status_code=201)
def create_gap(project_id: uuid.UUID, body: GapIn, c: Ctx = Depends(require("projects:write"))) -> EvidenceGap:
    p = svc.get_project(c, project_id, write=True)
    if body.resolved_by_claim_id:
        get_claim(c, body.resolved_by_claim_id)
    g = EvidenceGap(org_id=c.org_id, project_id=p.id, created_by=c.user.id, **body.model_dump())
    c.db.add(g)
    c.db.flush()
    record_ctx(c, "gap.created", "evidence_gap", g.id, project_id=str(p.id))
    return g


@router.put("/{project_id}/gaps/{gap_id}", response_model=GapOut)
def update_gap(project_id: uuid.UUID, gap_id: uuid.UUID, body: GapIn, c: Ctx = Depends(require("projects:write"))) -> EvidenceGap:
    p = svc.get_project(c, project_id, write=True)
    g = c.db.scalar(select(EvidenceGap).where(EvidenceGap.id == gap_id, EvidenceGap.project_id == p.id))
    if g is None:
        raise NotFound("Gap not found")
    if body.status == "resolved" and not body.resolved_by_claim_id:
        raise ValidationFailed("Resolving a gap requires the claim (evidence) that resolves it")
    if body.resolved_by_claim_id:
        get_claim(c, body.resolved_by_claim_id)
    for k, v in body.model_dump().items():
        setattr(g, k, v)
    record_ctx(c, "gap.updated", "evidence_gap", g.id, status=body.status)
    return g


# ------------------------------------------------------------------ assessments


@router.get("/{project_id}/assessments", response_model=list[AssessmentOut])
def list_assessments(project_id: uuid.UUID, c: Ctx = Depends(require("projects:read"))) -> list[Assessment]:
    p = svc.get_project(c, project_id)
    return list(
        c.db.scalars(
            select(Assessment)
            .where(Assessment.project_id == p.id)
            .order_by(Assessment.version.desc())
            .execution_options(populate_existing=True)
        )
    )


@router.post("/{project_id}/assessments", response_model=AssessmentOut, status_code=201)
def create_assessment(project_id: uuid.UUID, body: AssessmentIn, c: Ctx = Depends(require("assessments:write"))) -> dict:
    p = svc.get_project(c, project_id, write=True)
    a = svc.create_assessment(c, p, body.title, body.reviewer_conclusion)
    return {**AssessmentOut.model_validate(a).model_dump(), "snapshot": a.snapshot}


@router.get("/{project_id}/assessments/{assessment_id}")
def get_assessment(project_id: uuid.UUID, assessment_id: uuid.UUID, c: Ctx = Depends(require("projects:read"))) -> dict:
    p = svc.get_project(c, project_id)
    a = c.db.scalar(select(Assessment).where(Assessment.id == assessment_id, Assessment.project_id == p.id))
    if a is None:
        raise NotFound("Assessment not found")
    return {
        **AssessmentOut.model_validate(a).model_dump(mode="json"),
        "snapshot": a.snapshot,
        "integrity_verified": svc.verify_assessment(a),
    }


@router.post("/{project_id}/assessments/{assessment_id}/status", response_model=AssessmentOut)
def set_assessment_status(
    project_id: uuid.UUID, assessment_id: uuid.UUID, body: AssessmentStatusIn, c: Ctx = Depends(require("assessments:review"))
) -> Assessment:
    p = svc.get_project(c, project_id)
    a = c.db.scalar(select(Assessment).where(Assessment.id == assessment_id, Assessment.project_id == p.id))
    if a is None:
        raise NotFound("Assessment not found")
    return svc.change_assessment_status(c, a, body.status, body.reviewer_conclusion)


__all__ = ["router", "Claim"]
