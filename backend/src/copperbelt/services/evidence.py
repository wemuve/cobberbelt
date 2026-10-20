"""Claims, features, reviews and evidence links."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from geoalchemy2.shape import from_shape
from sqlalchemy import func, select

from copperbelt.errors import Conflict, NotFound, ValidationFailed
from copperbelt.geo.service import default_uncertainty_m, to_geojson, to_storage_geometry
from copperbelt.ingestion.vocabulary import canonical_commodity
from copperbelt.models.evidence import (
    Claim,
    ClaimType,
    Document,
    DocumentPage,
    EvidenceBasis,
    EvidenceRelation,
    ExtractionMethod,
    FeatureEvidence,
    GeoFeature,
    LocationPrecision,
    ReviewDecision,
    ReviewStatus,
)
from copperbelt.services.access import get_visible_document, visible_documents_clause
from copperbelt.services.audit import record_ctx

MANUAL_VERSION = "manual-1"


def norm_commodities(values: list[str]) -> list[str]:
    return sorted({canonical_commodity(v) or v.strip().lower() for v in values if v.strip()})


def get_claim(c, claim_id: uuid.UUID) -> Claim:  # noqa: ANN001
    claim = c.db.scalar(select(Claim).where(Claim.id == claim_id, Claim.org_id == c.org_id))
    if claim is None:
        raise NotFound("Claim not found")
    if claim.document_id is not None:
        get_visible_document(c, claim.document_id)  # inherits the source document's visibility
    return claim


def visible_claims_query(c):  # noqa: ANN001, ANN201
    return (
        select(Claim)
        .outerjoin(Document, Document.id == Claim.document_id)
        .where(Claim.org_id == c.org_id)
        .where((Claim.document_id.is_(None)) | visible_documents_clause(c))
    )


def create_claim(c, body) -> Claim:  # noqa: ANN001
    if body.document_id:
        doc = get_visible_document(c, body.document_id)
        if body.page_number is None:
            raise ValidationFailed("page_number is required when a document is cited")
        page = c.db.scalar(
            select(DocumentPage).where(
                DocumentPage.document_id == doc.id, DocumentPage.page_number == body.page_number, DocumentPage.is_current.is_(True)
            )
        )
        if page is None:
            raise ValidationFailed(f"Document has no processed page {body.page_number}")
        if body.char_start is not None and body.char_end is not None:
            if not (0 <= body.char_start < body.char_end <= len(page.text)):
                raise ValidationFailed("Character offsets fall outside the page text")
    elif body.evidence_basis == "historical_reported":
        raise ValidationFailed("A historical claim must cite a document and page")
    try:
        ctype = ClaimType(body.claim_type)
    except ValueError as exc:
        raise ValidationFailed(f"Unknown claim_type '{body.claim_type}'") from exc
    claim = Claim(
        org_id=c.org_id,
        document_id=body.document_id,
        page_number=body.page_number,
        char_start=body.char_start,
        char_end=body.char_end,
        claim_type=ctype,
        statement=body.statement,
        summary=body.summary,
        commodities=norm_commodities(body.commodities),
        attributes=body.attributes,
        reported_date=body.reported_date,
        evidence_basis=EvidenceBasis(body.evidence_basis),
        extraction_method=ExtractionMethod.manual,
        extractor_version=MANUAL_VERSION,
        confidence=body.confidence,
        created_by=c.user.id,
    )
    c.db.add(claim)
    c.db.flush()
    record_ctx(c, "claim.created", "claim", claim.id, document_id=str(body.document_id) if body.document_id else None)
    return claim


def review(c, entity: Claim | GeoFeature, entity_type: str, decision: str, comment: str | None) -> None:  # noqa: ANN001
    if decision in ("rejected", "needs_information") and not (comment and comment.strip()):
        raise ValidationFailed("A comment is required when rejecting or requesting information")
    previous = str(entity.review_status)
    entity.review_status = ReviewStatus(decision)
    c.db.add(
        ReviewDecision(
            org_id=c.org_id,
            entity_type=entity_type,
            entity_id=entity.id,
            previous_status=previous,
            decision=decision,
            comment=comment,
            reviewer_id=c.user.id,
            decided_at=datetime.now(UTC),
        )
    )
    record_ctx(c, f"{entity_type}.reviewed", entity_type, entity.id, previous=previous, decision=decision)


def review_history(c, entity_id: uuid.UUID) -> list[dict[str, Any]]:  # noqa: ANN001
    rows = c.db.scalars(
        select(ReviewDecision)
        .where(ReviewDecision.entity_id == entity_id, ReviewDecision.org_id == c.org_id)
        .order_by(ReviewDecision.decided_at)
    ).all()
    return [
        {
            "decision": r.decision,
            "previous_status": r.previous_status,
            "comment": r.comment,
            "reviewer_id": str(r.reviewer_id),
            "decided_at": r.decided_at.isoformat(),
        }
        for r in rows
    ]


def get_feature(c, feature_id: uuid.UUID) -> GeoFeature:  # noqa: ANN001
    f = c.db.scalar(select(GeoFeature).where(GeoFeature.id == feature_id, GeoFeature.org_id == c.org_id, GeoFeature.deleted_at.is_(None)))
    if f is None:
        raise NotFound("Feature not found")
    return f


def evidence_counts(c, feature_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:  # noqa: ANN001
    if not feature_ids:
        return {}
    rows = c.db.execute(
        select(FeatureEvidence.feature_id, FeatureEvidence.relation, func.count())
        .where(FeatureEvidence.feature_id.in_(feature_ids))
        .group_by(FeatureEvidence.feature_id, FeatureEvidence.relation)
    ).all()
    out: dict[uuid.UUID, dict[str, int]] = {}
    for fid, rel, n in rows:
        out.setdefault(fid, {})[str(rel)] = n
    return out


def feature_out(f: GeoFeature, counts: dict[str, int] | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": f.id,
        "name": f.name,
        "feature_type": f.feature_type,
        "commodities": f.commodities,
        "description": f.description,
        "geometry": to_geojson(f.geom),
        "source_crs": f.source_crs,
        "original_coordinates": f.original_coordinates,
        "location_precision": str(f.location_precision),
        "uncertainty_m": f.uncertainty_m,
        "country": f.country,
        "admin_area": f.admin_area,
        "review_status": str(f.review_status),
        "evidence_counts": counts or {},
        "warnings": warnings or [],
        "created_at": f.created_at,
    }


def create_feature(c, body) -> tuple[GeoFeature, list[str]]:  # noqa: ANN001
    warnings: list[str] = []
    geom_el, crs_label = None, None
    if body.geometry is not None:
        geom, crs_label, warnings = to_storage_geometry(body.geometry.model_dump(), body.source_crs)
        geom_el = from_shape(geom, srid=4326)
    elif body.location_precision not in ("unknown", "district"):
        raise ValidationFailed("A geometry is required unless the location is unknown or only known at district level")
    # Never invent an uncertainty: only a value the user states is stored. (Coordinate-resolution
    # defaults apply only when promoting a parsed coordinate, where the source's precision is known.)
    uncertainty = body.uncertainty_m
    if body.geometry is not None and uncertainty is None:
        warnings.append("No location uncertainty recorded; the map will flag this feature as uncertainty-unknown")
    f = GeoFeature(
        org_id=c.org_id,
        name=body.name,
        feature_type=body.feature_type,
        commodities=norm_commodities(body.commodities),
        description=body.description,
        geom=geom_el,
        source_crs=crs_label,
        original_coordinates=body.original_coordinates,
        location_precision=LocationPrecision(body.location_precision),
        uncertainty_m=uncertainty,
        country=body.country.upper() if body.country else None,
        admin_area=body.admin_area,
        created_by=c.user.id,
    )
    c.db.add(f)
    c.db.flush()
    record_ctx(c, "feature.created", "feature", f.id, crs=crs_label, precision=body.location_precision)
    return f, warnings


def link_evidence(c, feature: GeoFeature, claim_id: uuid.UUID, relation: str, note: str | None) -> FeatureEvidence:  # noqa: ANN001
    claim = get_claim(c, claim_id)
    if claim.review_status == ReviewStatus.rejected:
        raise Conflict("A rejected claim cannot be linked as evidence")
    link = c.db.scalar(select(FeatureEvidence).where(FeatureEvidence.feature_id == feature.id, FeatureEvidence.claim_id == claim.id))
    if link:
        link.relation, link.note = EvidenceRelation(relation), note
    else:
        link = FeatureEvidence(
            org_id=c.org_id, feature_id=feature.id, claim_id=claim.id, relation=EvidenceRelation(relation), note=note, created_by=c.user.id
        )
        c.db.add(link)
    c.db.flush()
    record_ctx(c, "feature.evidence_linked", "feature", feature.id, claim_id=str(claim.id), relation=relation)
    return link


def feature_evidence(c, feature: GeoFeature) -> dict[str, Any]:  # noqa: ANN001
    rows = c.db.execute(
        select(FeatureEvidence, Claim, Document.title)
        .join(Claim, Claim.id == FeatureEvidence.claim_id)
        .outerjoin(Document, Document.id == Claim.document_id)
        .where(FeatureEvidence.feature_id == feature.id)
        .where((Claim.document_id.is_(None)) | visible_documents_clause(c))
        .order_by(Claim.reported_date.nullslast(), Claim.created_at)
    ).all()
    hidden = (c.db.scalar(select(func.count()).select_from(FeatureEvidence).where(FeatureEvidence.feature_id == feature.id)) or 0) - len(
        rows
    )
    items = []
    for link, claim, title in rows:
        items.append(
            {
                "relation": str(link.relation),
                "note": link.note,
                "claim_id": str(claim.id),
                "claim_type": str(claim.claim_type),
                "statement": claim.statement,
                "commodities": claim.commodities,
                "evidence_basis": str(claim.evidence_basis),
                "review_status": str(claim.review_status),
                "extraction_method": str(claim.extraction_method),
                "confidence": claim.confidence,
                "reported_date": claim.reported_date.isoformat() if claim.reported_date else None,
                "document_id": str(claim.document_id) if claim.document_id else None,
                "document_title": title,
                "page_number": claim.page_number,
                "evidence_url": f"/api/v1/documents/{claim.document_id}/pages/{claim.page_number}" if claim.document_id else None,
            }
        )
    supports = [i for i in items if i["relation"] == "supports"]
    contradicts = [i for i in items if i["relation"] == "contradicts"]
    return {
        "feature_id": str(feature.id),
        "summary": {
            "supporting": len(supports),
            "contradicting": len(contradicts),
            "mentions": len(items) - len(supports) - len(contradicts),
            "accepted": sum(i["review_status"] == "accepted" for i in items),
            "unreviewed": sum(i["review_status"] == "unreviewed" for i in items),
            "independently_verified": sum(
                i["evidence_basis"] in ("independently_verified", "field_observation", "laboratory_result") for i in items
            ),
            "has_conflict": bool(supports and contradicts),
            "hidden_by_permissions": max(0, hidden),
        },
        "items": items,
    }


def promote_coordinate_claim(c, claim: Claim, body) -> tuple[GeoFeature, list[str]]:  # noqa: ANN001
    if claim.claim_type != ClaimType.coordinate:
        raise ValidationFailed("Only coordinate claims can be promoted to a feature")
    if claim.review_status != ReviewStatus.accepted:
        raise Conflict("Review and accept the coordinate claim before promoting it")
    lat, lon = claim.attributes.get("lat"), claim.attributes.get("lon")
    if lat is None or lon is None:
        raise ValidationFailed("Claim has no parsed coordinates")
    geom, _, warnings = to_storage_geometry({"type": "Point", "coordinates": [lon, lat]}, None)
    uncertainty = (
        body.uncertainty_m
        if body.uncertainty_m is not None
        else default_uncertainty_m("reported_coordinates", claim.attributes.get("stated_precision"))
    )
    warnings.append("Original datum unknown; stored as stated without datum shift. Verify before relying on sub-kilometre position.")
    f = GeoFeature(
        org_id=c.org_id,
        name=body.name,
        feature_type=body.feature_type,
        commodities=norm_commodities(body.commodities or claim.commodities),
        geom=from_shape(geom, srid=4326),
        source_crs=f"as stated (datum: {claim.attributes.get('datum', 'unknown')})",
        original_coordinates=claim.attributes.get("verbatim"),
        location_precision=LocationPrecision.reported_coordinates,
        uncertainty_m=uncertainty,
        description=body.datum_note,
        created_by=c.user.id,
    )
    c.db.add(f)
    c.db.flush()
    c.db.add(
        FeatureEvidence(
            org_id=c.org_id,
            feature_id=f.id,
            claim_id=claim.id,
            relation=EvidenceRelation.supports,
            note="Location promoted from this claim",
            created_by=c.user.id,
        )
    )
    record_ctx(c, "feature.promoted_from_claim", "feature", f.id, claim_id=str(claim.id))
    return f, warnings


PATCHABLE = {"name", "description", "commodities", "uncertainty_m", "location_precision", "admin_area", "country"}


def patch_feature(c, f: GeoFeature, changes: dict) -> GeoFeature:  # noqa: ANN001
    unknown = set(changes) - PATCHABLE
    if unknown:
        raise ValidationFailed(f"Fields cannot be edited here: {sorted(unknown)}")
    before = {k: (str(getattr(f, k)) if getattr(f, k) is not None else None) for k in changes}
    for k, v in changes.items():
        if k == "commodities":
            v = norm_commodities(v)
        elif k == "location_precision":
            v = LocationPrecision(v)
        elif k == "country" and v:
            v = v.upper()
        setattr(f, k, v)
    record_ctx(
        c,
        "feature.updated",
        "feature",
        f.id,
        before=before,
        after={k: (str(getattr(f, k)) if getattr(f, k) is not None else None) for k in changes},
    )
    return f
