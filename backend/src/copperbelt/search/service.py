"""Hybrid geological search.

Ranking combines (1) PostgreSQL full-text search over English-stemmed accent-free
text, French-stemmed text and unstemmed tokens, (2) commodity synonym expansion
(e.g. *cuivre* → copper, chalcopyrite, malachite…), (3) trigram similarity on
feature names and document titles, and (4) structured and spatial filters.

Semantic (vector) retrieval runs only when an embedding provider is configured;
results always report which retrieval modes were actually used.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from geoalchemy2.shape import from_shape
from sqlalchemy import Float, and_, cast, func, literal, or_, select

from copperbelt.geo.service import to_geojson, to_storage_geometry
from copperbelt.ingestion.vocabulary import canonical_commodity, expand_query_terms, normalise
from copperbelt.models.evidence import Claim, Document, DocumentPage, FeatureEvidence, GeoFeature, ReviewStatus, Source
from copperbelt.services.access import visible_documents_clause

SNIPPET_OPTS = "StartSel=«, StopSel=», MaxWords=40, MinWords=15, MaxFragments=2, FragmentDelimiter= … "


@dataclass
class SearchQuery:
    q: str | None = None
    commodities: list[str] = field(default_factory=list)
    country: str | None = None
    document_type: str | None = None
    source_id: uuid.UUID | None = None
    year_from: int | None = None
    year_to: int | None = None
    review_status: list[str] = field(default_factory=list)
    geometry: dict[str, Any] | None = None  # GeoJSON in EPSG:4326
    bbox: tuple[float, float, float, float] | None = None
    limit: int = 20
    offset: int = 0


def _expanded_query_text(q: str) -> str:
    """Turn 'cuivre kolwezi' into '(cuivre or copper or chalcopyrite ...) kolwezi' for websearch_to_tsquery."""
    out = []
    for token in q.split():
        if canonical_commodity(token):
            terms = expand_query_terms(token)
            out.append("(" + " or ".join(f'"{t}"' if " " in t else t for t in dict.fromkeys(terms)) + ")")
        else:
            out.append(token)
    return " ".join(out)


def _tsquery(q: str):  # noqa: ANN202
    expanded = _expanded_query_text(q)
    return (
        func.websearch_to_tsquery("english", normalise(expanded))
        .op("||")(func.websearch_to_tsquery("french", expanded))
        .op("||")(func.websearch_to_tsquery("simple", expanded))
    )


def _spatial_filter(sq: SearchQuery):  # noqa: ANN202
    if sq.geometry:
        geom, _, _ = to_storage_geometry(sq.geometry)
        return from_shape(geom, srid=4326)
    if sq.bbox:
        return func.ST_MakeEnvelope(*sq.bbox, 4326)
    return None


def search(c, sq: SearchQuery) -> dict[str, Any]:  # noqa: ANN001, C901
    db = c.db
    modes = ["structured_filters"]
    commodities = sorted({canonical_commodity(x) or x.lower() for x in sq.commodities})
    area = _spatial_filter(sq)
    if area is not None:
        modes.append("spatial")

    doc_filters = [visible_documents_clause(c), Document.status == "processed"]
    if sq.country:
        doc_filters.append(Document.country == sq.country.upper())
    if sq.document_type:
        doc_filters.append(Document.document_type == sq.document_type)
    if sq.source_id:
        doc_filters.append(Document.source_id == sq.source_id)
    if sq.year_from:
        doc_filters.append(Document.publication_year >= sq.year_from)
    if sq.year_to:
        doc_filters.append(Document.publication_year <= sq.year_to)

    # ---------- page hits (evidence passages)
    page_hits: list[dict[str, Any]] = []
    total_pages = 0
    if sq.q or commodities:
        text = " ".join(filter(None, [sq.q, " ".join(commodities)]))
        query = _tsquery(text)
        modes.append("full_text")
        rank = func.ts_rank_cd(DocumentPage.tsv, query, 32)
        conds = [DocumentPage.is_current.is_(True), DocumentPage.tsv.op("@@")(query), *doc_filters]
        if area is not None:
            # Documents with at least one claim linked to a feature in the area.
            in_area = (
                select(Claim.document_id)
                .join(FeatureEvidence, FeatureEvidence.claim_id == Claim.id)
                .join(GeoFeature, GeoFeature.id == FeatureEvidence.feature_id)
                .where(func.ST_Intersects(GeoFeature.geom, area))
            )
            conds.append(Document.id.in_(in_area))
        base = select(DocumentPage).join(Document, Document.id == DocumentPage.document_id).where(and_(*conds))
        total_pages = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.execute(
            select(
                DocumentPage.document_id,
                DocumentPage.page_number,
                DocumentPage.extraction_method,
                DocumentPage.ocr_confidence,
                Document.title,
                Document.publication_year,
                Document.classification,
                Source.name.label("source_name"),
                rank.label("rank"),
                func.ts_headline("simple", DocumentPage.text, query, SNIPPET_OPTS).label("snippet"),
            )
            .join(Document, Document.id == DocumentPage.document_id)
            .join(Source, Source.id == Document.source_id)
            .where(and_(*conds))
            .order_by(rank.desc(), DocumentPage.page_number)
            .limit(sq.limit)
            .offset(sq.offset)
        ).all()
        page_hits = [
            {
                "document_id": str(r.document_id),
                "document_title": r.title,
                "page_number": r.page_number,
                "snippet": r.snippet,
                "score": round(float(r.rank), 5),
                "publication_year": r.publication_year,
                "source": r.source_name,
                "classification": str(r.classification),
                "extraction_method": str(r.extraction_method),
                "ocr_confidence": r.ocr_confidence,
                "evidence_url": f"/api/v1/documents/{r.document_id}/pages/{r.page_number}",
            }
            for r in rows
        ]

    # ---------- feature hits
    fconds = [GeoFeature.org_id == c.org_id, GeoFeature.deleted_at.is_(None)]
    score = literal(0.0, Float)
    if sq.q:
        sim = func.similarity(GeoFeature.name, sq.q)
        fconds.append(
            or_(
                GeoFeature.name.op("%")(sq.q),
                GeoFeature.name.ilike(f"%{sq.q}%"),
                GeoFeature.commodities.overlap([canonical_commodity(t) or t.lower() for t in sq.q.split()]),
            )
        )
        score = cast(sim, Float)
        modes.append("trigram_names")
    if commodities:
        fconds.append(GeoFeature.commodities.overlap(commodities))
    if sq.country:
        fconds.append(GeoFeature.country == sq.country.upper())
    if sq.review_status:
        fconds.append(GeoFeature.review_status.in_([ReviewStatus(s) for s in sq.review_status]))
    if area is not None:
        fconds.append(func.ST_Intersects(GeoFeature.geom, area))
    feature_hits = []
    if sq.q or commodities or area is not None or sq.country:
        frows = db.execute(
            select(GeoFeature, score.label("score")).where(and_(*fconds)).order_by(score.desc(), GeoFeature.name).limit(sq.limit)
        ).all()
        feature_hits = [
            {
                "id": str(f.id),
                "name": f.name,
                "feature_type": f.feature_type,
                "commodities": f.commodities,
                "geometry": to_geojson(f.geom),
                "location_precision": str(f.location_precision),
                "uncertainty_m": f.uncertainty_m,
                "review_status": str(f.review_status),
                "score": round(float(s or 0), 4),
            }
            for f, s in frows
        ]

    from copperbelt.providers.registry import embeddings

    if embeddings.configured():
        modes.append("semantic:pending(embedding adapter not yet implemented)")
    else:
        modes.append("semantic:unavailable(no embedding provider configured)")
    return {
        "query": {"q": sq.q, "commodities": commodities, "expanded": _expanded_query_text(sq.q) if sq.q else None},
        "retrieval_modes": modes,
        "pages": {"total": total_pages, "limit": sq.limit, "offset": sq.offset, "items": page_hits},
        "features": {"items": feature_hits},
    }
