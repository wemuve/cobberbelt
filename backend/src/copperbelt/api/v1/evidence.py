from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from geoalchemy2.shape import from_shape
from sqlalchemy import func, select

from copperbelt.api.schemas import (
    AOIIn,
    AOIOut,
    ClaimIn,
    ClaimOut,
    EvidenceLinkIn,
    FeatureIn,
    FeatureOut,
    IntersectionIn,
    PromoteClaimIn,
    ReviewIn,
    SearchIn,
)
from copperbelt.errors import NotFound, ValidationFailed
from copperbelt.geo.service import area_km2, to_geojson, to_storage_geometry
from copperbelt.models.evidence import Claim, ClaimType, GeoFeature, ReviewStatus
from copperbelt.models.projects import AreaOfInterest
from copperbelt.search.service import SearchQuery, search
from copperbelt.security.deps import Ctx, require
from copperbelt.services import evidence as ev
from copperbelt.services.audit import record_ctx
from copperbelt.services.projects import get_project

router = APIRouter(tags=["evidence"])


# ------------------------------------------------------------------ claims


@router.get("/claims")
def list_claims(
    c: Ctx = Depends(require("documents:read")),
    document_id: uuid.UUID | None = None,
    page_number: int | None = None,
    claim_type: str | None = None,
    review_status: str | None = None,
    commodity: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    q = ev.visible_claims_query(c)
    if document_id:
        q = q.where(Claim.document_id == document_id)
    if page_number:
        q = q.where(Claim.page_number == page_number)
    if claim_type:
        q = q.where(Claim.claim_type == ClaimType(claim_type))
    if review_status:
        q = q.where(Claim.review_status == ReviewStatus(review_status))
    if commodity:
        q = q.where(Claim.commodities.any(ev.norm_commodities([commodity])[0]))
    total = c.db.scalar(select(func.count()).select_from(q.subquery()))
    rows = c.db.scalars(q.order_by(Claim.document_id, Claim.page_number, Claim.char_start).limit(limit).offset(offset)).all()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [ClaimOut.model_validate(r).model_dump(mode="json") for r in rows]}


@router.post("/claims", response_model=ClaimOut, status_code=201)
def create_claim(body: ClaimIn, c: Ctx = Depends(require("claims:write"))) -> Claim:
    return ev.create_claim(c, body)


@router.get("/claims/{claim_id}")
def get_claim(claim_id: uuid.UUID, c: Ctx = Depends(require("documents:read"))) -> dict:
    claim = ev.get_claim(c, claim_id)
    return {**ClaimOut.model_validate(claim).model_dump(mode="json"), "review_history": ev.review_history(c, claim.id)}


@router.post("/claims/{claim_id}/review", response_model=ClaimOut)
def review_claim(claim_id: uuid.UUID, body: ReviewIn, c: Ctx = Depends(require("claims:review"))) -> Claim:
    claim = ev.get_claim(c, claim_id)
    ev.review(c, claim, "claim", body.decision, body.comment)
    return claim


@router.post("/claims/{claim_id}/promote", response_model=FeatureOut, status_code=201)
def promote_claim(claim_id: uuid.UUID, body: PromoteClaimIn, c: Ctx = Depends(require("features:write"))) -> dict:
    claim = ev.get_claim(c, claim_id)
    f, warnings = ev.promote_coordinate_claim(c, claim, body)
    return ev.feature_out(f, {"supports": 1}, warnings)


# ------------------------------------------------------------------ geological features


@router.get("/geological-features", response_model=dict)
def list_features(
    c: Ctx = Depends(require("features:read")),
    commodity: str | None = None,
    feature_type: str | None = None,
    review_status: str | None = None,
    bbox: str | None = Query(default=None, description="minLon,minLat,maxLon,maxLat"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    q = select(GeoFeature).where(GeoFeature.org_id == c.org_id, GeoFeature.deleted_at.is_(None))
    if commodity:
        q = q.where(GeoFeature.commodities.any(ev.norm_commodities([commodity])[0]))
    if feature_type:
        q = q.where(GeoFeature.feature_type == feature_type)
    if review_status:
        q = q.where(GeoFeature.review_status == ReviewStatus(review_status))
    if bbox:
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
        except ValueError as exc:
            raise ValidationFailed("bbox must be minLon,minLat,maxLon,maxLat") from exc
        q = q.where(func.ST_Intersects(GeoFeature.geom, func.ST_MakeEnvelope(x0, y0, x1, y1, 4326)))
    total = c.db.scalar(select(func.count()).select_from(q.subquery()))
    rows = c.db.scalars(q.order_by(GeoFeature.name).limit(limit).offset(offset)).all()
    counts = ev.evidence_counts(c, [f.id for f in rows])
    return {"total": total, "limit": limit, "offset": offset,
            "items": [FeatureOut.model_validate(ev.feature_out(f, counts.get(f.id))).model_dump(mode="json") for f in rows]}


@router.post("/geological-features", response_model=FeatureOut, status_code=201)
def create_feature(body: FeatureIn, c: Ctx = Depends(require("features:write"))) -> dict:
    f, warnings = ev.create_feature(c, body)
    return ev.feature_out(f, {}, warnings)


@router.get("/geological-features/{feature_id}", response_model=FeatureOut)
def get_feature(feature_id: uuid.UUID, c: Ctx = Depends(require("features:read"))) -> dict:
    f = ev.get_feature(c, feature_id)
    return ev.feature_out(f, ev.evidence_counts(c, [f.id]).get(f.id))


@router.get("/geological-features/{feature_id}/evidence")
def get_feature_evidence(feature_id: uuid.UUID, c: Ctx = Depends(require("features:read"))) -> dict:
    return ev.feature_evidence(c, ev.get_feature(c, feature_id))


@router.post("/geological-features/{feature_id}/evidence", status_code=201)
def link_feature_evidence(feature_id: uuid.UUID, body: EvidenceLinkIn, c: Ctx = Depends(require("features:write"))) -> dict:
    f = ev.get_feature(c, feature_id)
    ev.link_evidence(c, f, body.claim_id, body.relation, body.note)
    return ev.feature_evidence(c, f)


@router.post("/geological-features/{feature_id}/review", response_model=FeatureOut)
def review_feature(feature_id: uuid.UUID, body: ReviewIn, c: Ctx = Depends(require("features:review"))) -> dict:
    f = ev.get_feature(c, feature_id)
    ev.review(c, f, "feature", body.decision, body.comment)
    return ev.feature_out(f, ev.evidence_counts(c, [f.id]).get(f.id))


# ------------------------------------------------------------------ search


@router.post("/search")
def run_search(body: SearchIn, c: Ctx = Depends(require("search:run"))) -> dict:
    if not (body.q or body.commodities or body.geometry or body.bbox or body.country):
        raise ValidationFailed("Provide a query, commodity, country or area to search")
    return search(c, SearchQuery(
        q=body.q, commodities=body.commodities, country=body.country, document_type=body.document_type,
        source_id=body.source_id, year_from=body.year_from, year_to=body.year_to, review_status=body.review_status,
        geometry=body.geometry.model_dump() if body.geometry else None, bbox=body.bbox, limit=body.limit, offset=body.offset,
    ))


# ------------------------------------------------------------------ geospatial


@router.get("/geo/layers")
def layers(c: Ctx = Depends(require("features:read"))) -> list[dict]:
    """Layers that actually have data or a configured provider. Nothing is listed as available speculatively."""
    n_features = c.db.scalar(select(func.count()).select_from(GeoFeature).where(GeoFeature.org_id == c.org_id, GeoFeature.deleted_at.is_(None), GeoFeature.geom.is_not(None)))
    n_aoi = c.db.scalar(select(func.count()).select_from(AreaOfInterest).where(AreaOfInterest.org_id == c.org_id))
    return [
        {"id": "geological_features", "title": "Geological features & occurrences", "status": "available", "count": n_features,
         "url": "/api/v1/geo/features"},
        {"id": "areas_of_interest", "title": "Project areas of interest", "status": "available", "count": n_aoi,
         "url": "/api/v1/geo/features?layer=areas_of_interest"},
        {"id": "mining_cadastre", "title": "Mining licences (ZM / CD)", "status": "blocked",
         "reason": "No authorised cadastre access configured; see docs/source-register.md"},
        {"id": "satellite_imagery", "title": "Satellite imagery (Planet)", "status": "blocked",
         "reason": "Planet API key and licence terms not yet provided"},
        {"id": "infrastructure", "title": "Infrastructure", "status": "planned", "reason": "Not in the first release"},
    ]


@router.get("/geo/features")
def geo_features(
    c: Ctx = Depends(require("features:read")),
    layer: str = Query("geological_features", pattern="^(geological_features|areas_of_interest)$"),
    bbox: str | None = None,
    limit: int = Query(2000, ge=1, le=10000),
) -> dict:
    """GeoJSON FeatureCollection for the map."""
    env = None
    if bbox:
        x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
        env = func.ST_MakeEnvelope(x0, y0, x1, y1, 4326)
    if layer == "areas_of_interest":
        q = select(AreaOfInterest).where(AreaOfInterest.org_id == c.org_id)
        if env is not None:
            q = q.where(func.ST_Intersects(AreaOfInterest.geom, env))
        rows = c.db.scalars(q.limit(limit)).all()
        feats = [{"type": "Feature", "id": str(a.id), "geometry": to_geojson(a.geom),
                  "properties": {"layer": layer, "name": a.name, "project_id": str(a.project_id) if a.project_id else None,
                                 "area_km2": a.area_km2}} for a in rows]
    else:
        q = select(GeoFeature).where(GeoFeature.org_id == c.org_id, GeoFeature.deleted_at.is_(None), GeoFeature.geom.is_not(None))
        if env is not None:
            q = q.where(func.ST_Intersects(GeoFeature.geom, env))
        rows = c.db.scalars(q.limit(limit)).all()
        counts = ev.evidence_counts(c, [f.id for f in rows])
        feats = []
        for f in rows:
            ec = counts.get(f.id, {})
            feats.append({"type": "Feature", "id": str(f.id), "geometry": to_geojson(f.geom), "properties": {
                "layer": layer, "name": f.name, "feature_type": f.feature_type, "commodities": f.commodities,
                "location_precision": str(f.location_precision), "uncertainty_m": f.uncertainty_m,
                "uncertainty_known": f.uncertainty_m is not None, "review_status": str(f.review_status),
                "supporting": ec.get("supports", 0), "contradicting": ec.get("contradicts", 0),
                "has_conflict": bool(ec.get("supports") and ec.get("contradicts")),
            }})
    return {"type": "FeatureCollection", "features": feats, "truncated": len(feats) >= limit}


@router.post("/geo/intersections")
def intersections(body: IntersectionIn, c: Ctx = Depends(require("features:read"))) -> dict:
    geom, crs_label, warnings = to_storage_geometry(body.geometry.model_dump(), body.source_crs)
    area = from_shape(geom, srid=4326)
    out: dict = {"input_crs": crs_label, "buffer_m": body.buffer_m, "warnings": warnings}
    if "features" in body.layers:
        cond = (func.ST_DWithin(func.Geography(GeoFeature.geom), func.Geography(area), body.buffer_m)
                if body.buffer_m else func.ST_Intersects(GeoFeature.geom, area))
        dist = func.ST_Distance(func.Geography(GeoFeature.geom), func.Geography(area))
        rows = c.db.execute(select(GeoFeature, dist.label("d")).where(
            GeoFeature.org_id == c.org_id, GeoFeature.deleted_at.is_(None), cond).order_by("d").limit(500)).all()
        out["features"] = [{**FeatureOut.model_validate(ev.feature_out(f)).model_dump(mode="json"),
                            "distance_m": round(float(d), 1),
                            # A feature whose uncertainty radius exceeds its distance may lie on either side of the boundary.
                            "boundary_ambiguous": f.uncertainty_m is None or f.uncertainty_m >= float(d)}
                           for f, d in rows]
    if "areas_of_interest" in body.layers:
        rows = c.db.scalars(select(AreaOfInterest).where(AreaOfInterest.org_id == c.org_id,
                                                         func.ST_Intersects(AreaOfInterest.geom, area))).all()
        out["areas_of_interest"] = [{"id": str(a.id), "name": a.name, "project_id": str(a.project_id) if a.project_id else None}
                                    for a in rows]
    out["not_checked"] = ["mining_cadastre (no authorised connector)", "protected_areas (no dataset loaded)"]
    return out


def _aoi_out(a: AreaOfInterest, warnings: list[str] | None = None) -> dict:
    return {"id": a.id, "name": a.name, "project_id": a.project_id, "geometry": to_geojson(a.geom),
            "source_crs": a.source_crs, "area_km2": a.area_km2, "warnings": warnings or [], "created_at": a.created_at}


@router.post("/areas-of-interest", response_model=AOIOut, status_code=201)
def create_aoi(body: AOIIn, c: Ctx = Depends(require("aoi:write"))) -> dict:
    if body.project_id:
        get_project(c, body.project_id, write=True)
    geom, crs_label, warnings = to_storage_geometry(body.geometry.model_dump(), body.source_crs)
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValidationFailed("An area of interest must be a Polygon or MultiPolygon")
    a = AreaOfInterest(org_id=c.org_id, project_id=body.project_id, name=body.name, geom=from_shape(geom, srid=4326),
                       source_crs=crs_label, area_km2=area_km2(geom), created_by=c.user.id)
    c.db.add(a)
    c.db.flush()
    record_ctx(c, "aoi.created", "area_of_interest", a.id, project_id=str(body.project_id) if body.project_id else None)
    return _aoi_out(a, warnings)


@router.get("/areas-of-interest/{aoi_id}", response_model=AOIOut)
def get_aoi(aoi_id: uuid.UUID, c: Ctx = Depends(require("features:read"))) -> dict:
    a = c.db.scalar(select(AreaOfInterest).where(AreaOfInterest.id == aoi_id, AreaOfInterest.org_id == c.org_id))
    if a is None:
        raise NotFound("Area of interest not found")
    if a.project_id:
        get_project(c, a.project_id)
    return _aoi_out(a)
