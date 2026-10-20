from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from copperbelt.api.schemas import DocumentOut, JobOut
from copperbelt.config import get_settings
from copperbelt.connectors import catalog as cat
from copperbelt.errors import Conflict, NotFound, PayloadTooLarge, ProviderUnavailable, ValidationFailed
from copperbelt.jobs.queue import enqueue
from copperbelt.models.evidence import CatalogRecord, Classification, Source
from copperbelt.models.ops import Job
from copperbelt.security.deps import Ctx, require
from copperbelt.services import documents as docs
from copperbelt.services.audit import record_ctx

router = APIRouter(tags=["catalogue"])


def _source(c: Ctx, source_id: uuid.UUID) -> Source:
    s = c.db.get(Source, source_id)
    if s is None or s.org_id != c.org_id:
        raise NotFound("Source not found")
    return s


@router.put("/sources/{source_id}/connector")
def configure_connector(source_id: uuid.UUID, body: cat.ConnectorConfig, c: Ctx = Depends(require("sources:write"))) -> dict:
    s = _source(c, source_id)
    s.connector_config = body.model_dump(mode="json")
    s.access_method = "api"
    record_ctx(c, "source.connector_configured", "source", s.id, base_url=str(body.base_url), fields=body.fields)
    return {"source_id": str(s.id), "connector": s.connector_config}


@router.post("/sources/{source_id}/connector/probe")
def probe_connector(source_id: uuid.UUID, c: Ctx = Depends(require("sources:write"))) -> dict:
    """Fetch one page and describe the catalogue's real structure. Nothing is stored."""
    s = _source(c, source_id)
    out = cat.probe(s)
    record_ctx(c, "source.connector_probed", "source", s.id, results_on_page=out["results_on_page"])
    return out


@router.post("/sources/{source_id}/harvest", response_model=JobOut, status_code=202)
def start_harvest(
    source_id: uuid.UUID, max_pages: int | None = Query(default=None, ge=1, le=10_000), c: Ctx = Depends(require("sources:write"))
) -> Job:
    s = _source(c, source_id)
    cat._require_permitted(s)  # fail fast with a clear reason
    job = enqueue(
        c.db,
        cat.HARVEST_JOB,
        {"source_id": str(s.id), "max_pages": max_pages},
        org_id=c.org_id,
        created_by=c.user.id,
        dedupe_key=f"harvest:{s.id}",
        max_attempts=3,
    )
    c.db.flush()
    record_ctx(c, "source.harvest_requested", "source", s.id, job_id=str(job.id))
    return job


class CatalogItem(BaseModel):
    id: uuid.UUID
    external_id: str
    title: str | None
    authors: list[str]
    year: int | None
    language: str | None
    document_type: str | None
    page_count: int | None
    landing_url: str | None
    file_url: str | None
    rights_statement: str | None
    document_id: uuid.UUID | None
    changed_upstream_at: str | None = None


@router.get("/sources/{source_id}/catalog")
def list_catalog(
    source_id: uuid.UUID,
    q: str | None = Query(default=None, max_length=200),
    imported: bool | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    c: Ctx = Depends(require("sources:read")),
) -> dict:
    s = _source(c, source_id)
    query = select(CatalogRecord).where(CatalogRecord.source_id == s.id)
    if q:
        query = query.where(CatalogRecord.title.ilike(f"%{q}%"))
    if imported is not None:
        query = query.where(CatalogRecord.document_id.is_not(None) if imported else CatalogRecord.document_id.is_(None))
    total = c.db.scalar(select(func.count()).select_from(query.subquery()))
    rows = c.db.scalars(query.order_by(CatalogRecord.year.nullslast(), CatalogRecord.title).limit(limit).offset(offset)).all()
    items = [
        CatalogItem(
            **{k: getattr(r, k) for k in CatalogItem.model_fields if k != "changed_upstream_at"},
            changed_upstream_at=r.changed_upstream_at.isoformat() if r.changed_upstream_at else None,
        ).model_dump(mode="json")
        for r in rows
    ]
    cfg = s.connector_config or {}
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
        "connector_verified_against_live_api": bool(cfg.get("verified_against_live_api")),
    }


class ImportIn(BaseModel):
    classification: str = Field(default="institutional")
    country: str | None = Field(default=None, min_length=2, max_length=2)


@router.post("/sources/{source_id}/catalog/{record_id}/import", status_code=201)
def import_record(
    source_id: uuid.UUID, record_id: uuid.UUID, body: ImportIn | None = None, c: Ctx = Depends(require("documents:upload"))
) -> dict:
    """Download one listed document from the catalogue's own host and ingest it like an upload."""
    s = _source(c, source_id)
    cfg = cat._require_permitted(s)
    rec = c.db.scalar(select(CatalogRecord).where(CatalogRecord.id == record_id, CatalogRecord.source_id == s.id))
    if rec is None:
        raise NotFound("Catalogue record not found")
    if rec.document_id:
        raise Conflict("Already imported", detail={"document_id": str(rec.document_id)})
    if not rec.file_url:
        raise ValidationFailed("This record has no file URL in the source catalogue (check the connector's file_url mapping)")
    if urlparse(rec.file_url).netloc != urlparse(str(cfg.base_url)).netloc:
        raise ValidationFailed("The file is hosted elsewhere than the catalogue; import it manually after checking its terms")
    try:
        cls = Classification(body.classification if body else "institutional")
    except ValueError as exc:
        raise ValidationFailed("Unknown classification") from exc
    limit = get_settings().max_upload_bytes
    fd, name = tempfile.mkstemp(prefix="cbi-import-")
    path = Path(name)
    try:
        with open(fd, "wb") as out, httpx.Client(timeout=120, transport=cat.TRANSPORT, headers={"User-Agent": cat.USER_AGENT}) as http:
            try:
                with http.stream("GET", rec.file_url) as r:
                    if r.status_code >= 400:
                        raise ProviderUnavailable(f"Source returned {r.status_code} for the file", code="provider_error")
                    size = 0
                    for chunk in r.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise PayloadTooLarge("Remote file exceeds the upload size limit")
                        out.write(chunk)
            except httpx.TransportError as exc:
                raise ProviderUnavailable(f"Could not download the file: {type(exc).__name__}", code="provider_unreachable") from exc
        filename = Path(urlparse(rec.file_url).path).name or f"{rec.external_id}.pdf"
        doc, created = docs.create_document(
            c.db,
            org_id=c.org_id,
            user_id=c.user.id,
            source_id=s.id,
            upload_path=path,
            filename=filename[:200],
            request_id=c.request_id,
            fields={
                "title": (rec.title or filename)[:500],
                "authors": rec.authors,
                "publication_year": rec.year,
                "document_type": rec.document_type,
                "source_reference": f"catalog:{rec.external_id}",
                "language": rec.language,
                "classification": cls,
                "country": body.country.upper() if body and body.country else None,
            },
        )
    finally:
        path.unlink(missing_ok=True)
    rec.document_id = doc.id
    record_ctx(c, "catalog.record_imported", "catalog_record", rec.id, document_id=str(doc.id), external_id=rec.external_id)
    return {"document": DocumentOut.model_validate(doc).model_dump(mode="json", by_alias=True), "created": created}
