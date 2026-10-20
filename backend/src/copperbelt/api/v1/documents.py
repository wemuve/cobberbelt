from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select

from copperbelt.api.schemas import (
    DocumentOut,
    DocumentPatch,
    DocumentUploadOut,
    JobOut,
    PageOut,
    PageSummary,
    ProcessIn,
    SourceIn,
    SourceOut,
)
from copperbelt.config import get_settings
from copperbelt.errors import Conflict, NotFound, ValidationFailed
from copperbelt.ingestion.extract import render_pdf_page_png
from copperbelt.ingestion.validation import clean_filename
from copperbelt.models.evidence import (
    Classification,
    Document,
    DocumentAccessGrant,
    DocumentPage,
    DocumentStatus,
    Source,
)
from copperbelt.models.identity import Membership
from copperbelt.models.ops import Job
from copperbelt.security.deps import Ctx, require
from copperbelt.services import documents as svc
from copperbelt.services.access import get_visible_document, visible_documents_clause
from copperbelt.services.audit import record_ctx
from copperbelt.storage import get_store

router = APIRouter(tags=["documents"])


# ------------------------------------------------------------------ sources


@router.get("/sources", response_model=list[SourceOut])
def list_sources(c: Ctx = Depends(require("sources:read"))) -> list[Source]:
    return list(c.db.scalars(select(Source).where(Source.org_id == c.org_id).order_by(Source.name)))


@router.post("/sources", response_model=SourceOut, status_code=201)
def create_source(body: SourceIn, c: Ctx = Depends(require("sources:write"))) -> Source:
    if c.db.scalar(select(Source).where(Source.org_id == c.org_id, Source.name == body.name)):
        raise Conflict("A source with this name already exists")
    src = Source(org_id=c.org_id, created_by=c.user.id, **body.model_dump())
    c.db.add(src)
    c.db.flush()
    record_ctx(c, "source.created", "source", src.id, access_status=body.access_status, licence=body.licence)
    return src


@router.patch("/sources/{source_id}", response_model=SourceOut)
def update_source(source_id: uuid.UUID, body: SourceIn, c: Ctx = Depends(require("sources:write"))) -> Source:
    src = c.db.get(Source, source_id)
    if src is None or src.org_id != c.org_id:
        raise NotFound("Source not found")
    before = {"access_status": str(src.access_status), "licence": src.licence}
    for k, v in body.model_dump().items():
        setattr(src, k, v)
    record_ctx(c, "source.updated", "source", src.id, before=before,
               after={"access_status": body.access_status, "licence": body.licence})
    return src


# ------------------------------------------------------------------ documents


@router.post("/documents", response_model=DocumentUploadOut, status_code=201)
def upload_document(
    file: Annotated[UploadFile, File(description="PDF, PNG, JPEG, TIFF or UTF-8 text")],
    source_id: Annotated[uuid.UUID, Form()],
    title: Annotated[str, Form(min_length=1, max_length=500)],
    classification: Annotated[str, Form()] = "institutional",
    publication_year: Annotated[int | None, Form(ge=1800, le=2100)] = None,
    authors: Annotated[str | None, Form(description="Semicolon-separated")] = None,
    document_type: Annotated[str | None, Form()] = None,
    source_reference: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form(min_length=2, max_length=2)] = None,
    c: Ctx = Depends(require("documents:upload")),
) -> DocumentUploadOut:
    try:
        cls = Classification(classification)
    except ValueError as exc:
        raise ValidationFailed(f"Unknown classification '{classification}'") from exc
    path = svc.spool_upload(file.file, get_settings().max_upload_bytes)
    try:
        doc, created = svc.create_document(
            c.db, org_id=c.org_id, user_id=c.user.id, source_id=source_id, upload_path=path,
            filename=clean_filename(file.filename), request_id=c.request_id,
            fields={
                "title": title, "classification": cls, "publication_year": publication_year,
                "authors": [a.strip() for a in (authors or "").split(";") if a.strip()],
                "document_type": document_type, "source_reference": source_reference,
                "language": language, "country": country.upper() if country else None,
            },
        )
    finally:
        path.unlink(missing_ok=True)
    job = c.db.scalar(select(Job).where(Job.dedupe_key == f"process:{doc.id}", Job.org_id == c.org_id)) if created else None
    if not created:
        doc = get_visible_document(c, doc.id)  # duplicate: only reveal it if the caller may see it
    return DocumentUploadOut(document=DocumentOut.model_validate(doc), created=created, job_id=job.id if job else None)


@router.get("/documents")
def list_documents(
    c: Ctx = Depends(require("documents:read")),
    status: str | None = None,
    source_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    query = select(Document).where(visible_documents_clause(c))
    if status:
        query = query.where(Document.status == status)
    if source_id:
        query = query.where(Document.source_id == source_id)
    if q:
        query = query.where(Document.title.ilike(f"%{q}%"))
    total = c.db.scalar(select(func.count()).select_from(query.subquery()))
    items = c.db.scalars(query.order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [DocumentOut.model_validate(d).model_dump(mode="json", by_alias=True) for d in items]}


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, c: Ctx = Depends(require("documents:read"))) -> Document:
    return get_visible_document(c, document_id)


@router.patch("/documents/{document_id}", response_model=DocumentOut)
def patch_document(document_id: uuid.UUID, body: DocumentPatch, c: Ctx = Depends(require("documents:upload"))) -> Document:
    doc = get_visible_document(c, document_id)
    changes = body.model_dump(exclude_unset=True)
    if "classification" in changes and not c.can("documents:grant"):
        raise ValidationFailed("Only administrators can change a document's classification")
    for k, v in changes.items():
        setattr(doc, k, Classification(v) if k == "classification" else v)
    record_ctx(c, "document.updated", "document", doc.id, fields=sorted(changes))
    return doc


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, c: Ctx = Depends(require("documents:delete"))) -> None:
    """Soft delete: hidden everywhere, original retained for the audit/retention period."""
    from datetime import UTC, datetime

    doc = get_visible_document(c, document_id)
    doc.deleted_at = datetime.now(UTC)
    record_ctx(c, "document.deleted", "document", doc.id)


@router.post("/documents/{document_id}/grants", status_code=204)
def grant_access(document_id: uuid.UUID, user_id: uuid.UUID, c: Ctx = Depends(require("documents:grant"))) -> None:
    doc = get_visible_document(c, document_id)
    member = c.db.scalar(select(Membership).where(Membership.user_id == user_id, Membership.org_id == c.org_id,
                                                  Membership.is_active.is_(True)))
    if member is None:
        raise NotFound("User is not a member of this organisation")
    if not c.db.scalar(select(DocumentAccessGrant).where(DocumentAccessGrant.document_id == doc.id,
                                                          DocumentAccessGrant.user_id == user_id)):
        c.db.add(DocumentAccessGrant(org_id=c.org_id, document_id=doc.id, user_id=user_id, granted_by=c.user.id))
    record_ctx(c, "document.access_granted", "document", doc.id, user_id=str(user_id))


@router.delete("/documents/{document_id}/grants/{user_id}", status_code=204)
def revoke_access(document_id: uuid.UUID, user_id: uuid.UUID, c: Ctx = Depends(require("documents:grant"))) -> None:
    doc = get_visible_document(c, document_id)
    grant = c.db.scalar(select(DocumentAccessGrant).where(DocumentAccessGrant.document_id == doc.id,
                                                           DocumentAccessGrant.user_id == user_id))
    if grant:
        c.db.delete(grant)
    record_ctx(c, "document.access_revoked", "document", doc.id, user_id=str(user_id))


@router.get("/documents/{document_id}/file")
def download_original(document_id: uuid.UUID, c: Ctx = Depends(require("documents:read"))) -> StreamingResponse:
    doc = get_visible_document(c, document_id)
    record_ctx(c, "document.downloaded", "document", doc.id)
    stream = get_store().open(doc.storage_key)
    return StreamingResponse(
        stream, media_type=doc.content_type,
        headers={"Content-Disposition": f'attachment; filename="{doc.original_filename}"',
                 "X-Content-SHA256": doc.sha256, "Cache-Control": "private, no-store"},
    )


@router.get("/documents/{document_id}/pages", response_model=list[PageSummary])
def list_pages(document_id: uuid.UUID, c: Ctx = Depends(require("documents:read"))) -> list[DocumentPage]:
    doc = get_visible_document(c, document_id)
    return list(c.db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id, DocumentPage.is_current.is_(True))
                             .order_by(DocumentPage.page_number)))


@router.get("/documents/{document_id}/pages/{page_number}", response_model=PageOut)
def get_page(document_id: uuid.UUID, page_number: int, c: Ctx = Depends(require("documents:read"))) -> DocumentPage:
    doc = get_visible_document(c, document_id)
    page = c.db.scalar(select(DocumentPage).where(DocumentPage.document_id == doc.id, DocumentPage.page_number == page_number,
                                                  DocumentPage.is_current.is_(True)))
    if page is None:
        raise NotFound("Page not found or document not yet processed")
    return page


@router.get("/documents/{document_id}/pages/{page_number}/image")
def page_image(document_id: uuid.UUID, page_number: int, c: Ctx = Depends(require("documents:read"))) -> Response:
    """Rendered page image of the original, so reviewers can compare extracted text with the source."""
    doc = get_visible_document(c, document_id)
    with get_store().open(doc.storage_key) as f:
        data = f.read()
    if doc.content_type == "application/pdf":
        try:
            png = render_pdf_page_png(data, page_number)
        except IndexError as exc:
            raise NotFound("Page out of range") from exc
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
    if doc.content_type.startswith("image/") and page_number == 1:
        return Response(data, media_type=doc.content_type, headers={"Cache-Control": "private, max-age=300"})
    raise NotFound("No page image available for this document type")


@router.post("/documents/{document_id}/process", response_model=JobOut, status_code=202)
def process(document_id: uuid.UUID, body: ProcessIn | None = None, c: Ctx = Depends(require("documents:process"))) -> Job:
    doc = get_visible_document(c, document_id)
    job = svc.request_processing(c.db, doc, c.user.id, force_ocr=bool(body and body.force_ocr))
    c.db.flush()
    record_ctx(c, "document.process_requested", "document", doc.id, job_id=str(job.id), force_ocr=bool(body and body.force_ocr))
    return job


@router.get("/documents/{document_id}/status")
def status(document_id: uuid.UUID, c: Ctx = Depends(require("documents:read"))) -> dict:
    doc = get_visible_document(c, document_id)
    job = c.db.scalar(select(Job).where(Job.org_id == c.org_id, Job.payload["document_id"].astext == str(doc.id))
                      .order_by(Job.created_at.desc()).limit(1))
    return {
        "document_id": str(doc.id), "status": str(doc.status), "processing_error": doc.processing_error,
        "malware_scan": doc.malware_scan, "processing_version": doc.processing_version,
        "last_processing": (doc.metadata_ or {}).get("last_processing"),
        "job": JobOut.model_validate(job).model_dump(mode="json") if job else None,
        "retryable": doc.status in (DocumentStatus.failed,) or (job is not None and str(job.status) in ("dead", "failed")),
    }
