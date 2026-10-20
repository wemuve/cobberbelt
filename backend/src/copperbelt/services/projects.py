"""Project workspaces, dossiers and hash-sealed assessments."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select

from copperbelt.errors import Conflict, Forbidden, NotFound, ValidationFailed
from copperbelt.geo.service import to_geojson
from copperbelt.models.evidence import Claim, Document, FeatureEvidence, GeoFeature
from copperbelt.models.projects import (
    AreaOfInterest,
    Assessment,
    AssessmentStatus,
    EvidenceGap,
    Project,
    ProjectDocument,
    ProjectFeature,
    ProjectMember,
    ProjectRole,
    Risk,
)
from copperbelt.services.access import visible_documents_clause
from copperbelt.services.audit import record_ctx

ASSESSMENT_METHOD_VERSION = "assessment-method-1.0"

DISCLAIMER = (
    "This assessment compiles evidence held in the platform at the time of generation. It is analytical decision "
    "support, not a mineral resource or reserve statement, legal opinion on title, environmental compliance "
    "certification, feasibility study or investment recommendation. Historical reported results have not been "
    "independently verified unless marked otherwise. Checks listed under 'not_checked' were not performed."
)

RISK_SCORING = {
    "formula": "score = severity × likelihood (each 1–5, assigned by the analyst)",
    "bands": {"low": "1–4", "medium": "5–9", "high": "10–15", "critical": "16–25"},
    "note": "Scores are analyst judgements recorded with their evidence; the platform does not compute them.",
}


def risk_band(score: int) -> str:
    return "critical" if score >= 16 else "high" if score >= 10 else "medium" if score >= 5 else "low"


def project_role(c, project_id: uuid.UUID) -> ProjectRole | None:  # noqa: ANN001
    return c.db.scalar(select(ProjectMember.role).where(ProjectMember.project_id == project_id, ProjectMember.user_id == c.user.id))


def get_project(c, project_id: uuid.UUID, *, write: bool = False) -> Project:  # noqa: ANN001
    """Projects are visible to their members and to organisation admins/owners."""
    p = c.db.scalar(select(Project).where(Project.id == project_id, Project.org_id == c.org_id, Project.archived_at.is_(None)))
    if p is None:
        raise NotFound("Project not found")
    admin = c.can("members:manage")
    role = project_role(c, project_id)
    if role is None and not admin:
        raise NotFound("Project not found")
    if write and not admin and role == ProjectRole.reader:
        raise Forbidden("Project readers cannot modify the project")
    return p


def visible_projects_query(c):  # noqa: ANN001, ANN201
    q = select(Project).where(Project.org_id == c.org_id, Project.archived_at.is_(None))
    if not c.can("members:manage"):
        q = q.where(Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == c.user.id)))
    return q


def create_project(c, body) -> Project:  # noqa: ANN001
    from copperbelt.services.evidence import norm_commodities

    data = body.model_dump()
    data["commodities"] = norm_commodities(data["commodities"])
    if data.get("country"):
        data["country"] = data["country"].upper()
    p = Project(org_id=c.org_id, created_by=c.user.id, **data)
    c.db.add(p)
    c.db.flush()
    c.db.add(ProjectMember(org_id=c.org_id, project_id=p.id, user_id=c.user.id, role=ProjectRole.lead))
    record_ctx(c, "project.created", "project", p.id, name=p.name)
    return p


def dossier(c, p: Project) -> dict[str, Any]:  # noqa: ANN001, C901
    db = c.db
    aois = db.scalars(select(AreaOfInterest).where(AreaOfInterest.project_id == p.id).order_by(AreaOfInterest.created_at)).all()

    linked_ids = set(db.scalars(select(ProjectFeature.feature_id).where(ProjectFeature.project_id == p.id)))
    in_aoi: dict[uuid.UUID, list[str]] = {}
    for a in aois:
        for fid in db.scalars(
            select(GeoFeature.id).where(
                GeoFeature.org_id == c.org_id, GeoFeature.deleted_at.is_(None), func.ST_Intersects(GeoFeature.geom, a.geom)
            )
        ):
            in_aoi.setdefault(fid, []).append(a.name)
    feature_ids = linked_ids | set(in_aoi)
    features = db.scalars(select(GeoFeature).where(GeoFeature.id.in_(feature_ids)).order_by(GeoFeature.name)).all() if feature_ids else []

    links = db.execute(
        select(FeatureEvidence.feature_id, FeatureEvidence.relation, Claim)
        .join(Claim, Claim.id == FeatureEvidence.claim_id)
        .outerjoin(Document, Document.id == Claim.document_id)
        .where(FeatureEvidence.feature_id.in_(feature_ids or [uuid.uuid4()]))
        .where(or_(Claim.document_id.is_(None), visible_documents_clause(c)))
    ).all()
    ev_by_feature: dict[uuid.UUID, list[dict[str, Any]]] = {}
    claim_ids: set[uuid.UUID] = set()
    for fid, rel, cl in links:
        claim_ids.add(cl.id)
        ev_by_feature.setdefault(fid, []).append(
            {
                "claim_id": str(cl.id),
                "relation": str(rel),
                "statement": cl.statement,
                "review_status": str(cl.review_status),
                "evidence_basis": str(cl.evidence_basis),
                "document_id": str(cl.document_id) if cl.document_id else None,
                "page_number": cl.page_number,
                "reported_date": cl.reported_date.isoformat() if cl.reported_date else None,
            }
        )

    docs = db.execute(
        select(Document.id, Document.title, Document.publication_year, Document.classification, Document.sha256)
        .join(ProjectDocument, ProjectDocument.document_id == Document.id)
        .where(ProjectDocument.project_id == p.id, visible_documents_clause(c))
    ).all()
    risks = db.scalars(select(Risk).where(Risk.project_id == p.id).order_by(Risk.created_at)).all()
    gaps = db.scalars(select(EvidenceGap).where(EvidenceGap.project_id == p.id).order_by(EvidenceGap.created_at)).all()

    feature_rows = []
    for f in features:
        evs = ev_by_feature.get(f.id, [])
        feature_rows.append(
            {
                "id": str(f.id),
                "name": f.name,
                "feature_type": f.feature_type,
                "commodities": f.commodities,
                "geometry": to_geojson(f.geom),
                "location_precision": str(f.location_precision),
                "uncertainty_m": f.uncertainty_m,
                "review_status": str(f.review_status),
                "in_areas": in_aoi.get(f.id, []),
                "explicitly_linked": f.id in linked_ids,
                "evidence": evs,
                "has_conflict": any(e["relation"] == "supports" for e in evs) and any(e["relation"] == "contradicts" for e in evs),
            }
        )

    all_ev = [e for evs in ev_by_feature.values() for e in evs]
    quality = {
        "features": len(features),
        "features_without_evidence": sum(1 for r in feature_rows if not r["evidence"]),
        "features_with_conflicts": sum(1 for r in feature_rows if r["has_conflict"]),
        "features_uncertainty_unknown": sum(1 for f in features if f.uncertainty_m is None),
        "evidence_items": len(all_ev),
        "evidence_accepted": sum(e["review_status"] == "accepted" for e in all_ev),
        "evidence_unreviewed": sum(e["review_status"] == "unreviewed" for e in all_ev),
        "evidence_historical_only": sum(e["evidence_basis"] == "historical_reported" for e in all_ev),
        "evidence_independently_verified": sum(
            e["evidence_basis"] in ("independently_verified", "field_observation", "laboratory_result") for e in all_ev
        ),
    }

    # Automatic gap detection: facts about the dossier, never conclusions about the project.
    auto_gaps = []
    if not aois:
        auto_gaps.append("No area of interest defined: spatial checks cannot run.")
    if quality["features_without_evidence"]:
        auto_gaps.append(f"{quality['features_without_evidence']} feature(s) have no linked source evidence.")
    if quality["features_with_conflicts"]:
        auto_gaps.append(f"{quality['features_with_conflicts']} feature(s) have conflicting evidence that needs resolution.")
    if quality["evidence_items"] and not quality["evidence_independently_verified"]:
        auto_gaps.append("All evidence is historical or unverified; no field observation or laboratory result is linked.")
    if quality["evidence_unreviewed"]:
        auto_gaps.append(f"{quality['evidence_unreviewed']} evidence item(s) are unreviewed.")
    if quality["features_uncertainty_unknown"]:
        auto_gaps.append(f"{quality['features_uncertainty_unknown']} feature(s) have unknown location uncertainty.")

    return {
        "project": {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "country": p.country,
            "commodities": p.commodities,
            "stage": p.stage,
        },
        "areas_of_interest": [{"id": str(a.id), "name": a.name, "geometry": to_geojson(a.geom), "area_km2": a.area_km2} for a in aois],
        "features": feature_rows,
        "documents": [
            {
                "id": str(d.id),
                "title": d.title,
                "publication_year": d.publication_year,
                "classification": str(d.classification),
                "sha256": d.sha256,
            }
            for d in docs
        ],
        "risks": [
            {
                "id": str(r.id),
                "title": r.title,
                "category": r.category,
                "description": r.description,
                "severity": r.severity,
                "likelihood": r.likelihood,
                "score": r.severity * r.likelihood,
                "band": risk_band(r.severity * r.likelihood),
                "status": str(r.status),
                "claim_ids": [str(x) for x in r.claim_ids],
                "verification_action": r.verification_action,
                "due_date": r.due_date.isoformat() if r.due_date else None,
            }
            for r in risks
        ],
        "evidence_gaps": [
            {
                "id": str(g.id),
                "topic": g.topic,
                "why_it_matters": g.why_it_matters,
                "verification_action": g.verification_action,
                "status": g.status,
            }
            for g in gaps
        ],
        "detected_gaps": auto_gaps,
        "evidence_quality": quality,
        "not_checked": [
            "Mining title / licence status (no authorised cadastre connector configured)",
            "Satellite observations (Planet integration not configured)",
            "Infrastructure proximity (no infrastructure dataset loaded)",
            "Environmental and protected-area constraints (no dataset loaded)",
        ],
        "risk_scoring": RISK_SCORING,
        "claim_count": len(claim_ids),
    }


def _seal(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_assessment(c, p: Project, title: str, reviewer_conclusion: str | None) -> Assessment:  # noqa: ANN001
    version = (c.db.scalar(select(func.max(Assessment.version)).where(Assessment.project_id == p.id)) or 0) + 1
    snapshot = {
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": str(c.user.id),
        "method_version": ASSESSMENT_METHOD_VERSION,
        "disclaimer": DISCLAIMER,
        "dossier": dossier(c, p),
    }
    a = Assessment(
        org_id=c.org_id,
        project_id=p.id,
        version=version,
        title=title,
        method_version=ASSESSMENT_METHOD_VERSION,
        reviewer_conclusion=reviewer_conclusion,
        snapshot=snapshot,
        content_sha256=_seal(snapshot),
        created_by=c.user.id,
    )
    c.db.add(a)
    c.db.flush()
    record_ctx(c, "assessment.created", "assessment", a.id, project_id=str(p.id), version=version, sha256=a.content_sha256)
    return a


def change_assessment_status(c, a: Assessment, status: str, conclusion: str | None) -> Assessment:  # noqa: ANN001
    current = AssessmentStatus(a.status)
    if current in (AssessmentStatus.approved, AssessmentStatus.superseded):
        raise Conflict("Approved assessments are immutable; create a new version")
    if status == "in_review":
        if current != AssessmentStatus.draft:
            raise Conflict("Only drafts can be sent for review")
        if conclusion is not None:
            a.reviewer_conclusion = conclusion
    elif status == "approved":
        c.require("assessments:approve")
        if current != AssessmentStatus.in_review:
            raise Conflict("Send the assessment for review before approving it")
        if a.created_by == c.user.id:
            raise Forbidden("Separation of duties: the author cannot approve their own assessment")
        if conclusion is not None:
            a.reviewer_conclusion = conclusion
        if not (a.reviewer_conclusion and a.reviewer_conclusion.strip()):
            raise ValidationFailed("An approved assessment must record the reviewer's professional conclusion")
        for prev in c.db.scalars(
            select(Assessment).where(Assessment.project_id == a.project_id, Assessment.status == AssessmentStatus.approved)
        ):
            prev.status = AssessmentStatus.superseded
        a.status = AssessmentStatus.approved
        a.approved_by = c.user.id
        a.approved_at = datetime.now(UTC)
    if status == "in_review":
        a.status = AssessmentStatus.in_review
    record_ctx(c, f"assessment.{status}", "assessment", a.id, sha256=a.content_sha256)
    return a


def verify_assessment(a: Assessment) -> bool:
    return _seal(a.snapshot) == a.content_sha256
