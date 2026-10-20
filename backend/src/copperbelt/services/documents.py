"""Document upload and asynchronous processing."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from copperbelt.config import get_settings
from copperbelt.errors import Conflict, NotFound, PayloadTooLarge, ValidationFailed
from copperbelt.ingestion import extract as ex
from copperbelt.ingestion.rules import RULES_VERSION, extract_candidates
from copperbelt.ingestion.validation import (
    SUPPORTED_TYPES,
    ScanResult,
    check_pdf_safety,
    clamav_scan,
    sniff_content_type,
)
from copperbelt.ingestion.vocabulary import normalise
from copperbelt.jobs.queue import JobContext, PermanentJobError, enqueue, handler
from copperbelt.models.evidence import (
    INGESTIBLE_ACCESS,
    Claim,
    ClaimType,
    Document,
    DocumentPage,
    DocumentStatus,
    ExtractionMethod,
    PageChunk,
    ReviewStatus,
    Source,
)
from copperbelt.services.audit import record
from copperbelt.storage import get_store, sha256_file

log = structlog.get_logger()
PROCESS_JOB = "document.process"
PIPELINE_VERSION = f"{ex.EXTRACTOR_VERSION}+{RULES_VERSION}"
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200


def spool_upload(stream: BinaryIO, max_bytes: int) -> Path:
    """Copy an upload to a temp file, enforcing the size limit while streaming."""
    fd, name = tempfile.mkstemp(prefix="cbi-upload-")
    path = Path(name)
    total = 0
    with open(fd, "wb") as out:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                out.close()
                path.unlink(missing_ok=True)
                raise PayloadTooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
            out.write(chunk)
    if total == 0:
        path.unlink(missing_ok=True)
        raise ValidationFailed("Uploaded file is empty", code="empty_file")
    return path


def create_document(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    upload_path: Path,
    filename: str,
    fields: dict,
    request_id: str | None = None,
) -> tuple[Document, bool]:
    """Store the original and queue processing in one transaction. Returns (document, created)."""
    source = db.get(Source, source_id)
    if source is None or source.org_id != org_id:
        raise NotFound("Source not found")
    if source.access_status not in INGESTIBLE_ACCESS:
        raise Conflict(
            f"Source '{source.name}' has access status '{source.access_status}'. Record the licence or "
            "authorisation on the source before ingesting its documents.",
            code="source_not_authorised",
        )

    content_type = sniff_content_type(upload_path)
    if content_type not in SUPPORTED_TYPES:
        raise ValidationFailed(f"Unsupported file type {content_type}")
    digest = sha256_file(upload_path)
    existing = db.scalar(select(Document).where(Document.org_id == org_id, Document.sha256 == digest))
    if existing is not None:
        return existing, False

    warnings = check_pdf_safety(upload_path) if content_type == "application/pdf" else []
    key = f"{org_id}/documents/{digest}"
    get_store().put_file(key, upload_path, content_type)

    doc = Document(
        org_id=org_id,
        source_id=source_id,
        original_filename=filename,
        content_type=content_type,
        size_bytes=upload_path.stat().st_size,
        sha256=digest,
        storage_key=key,
        status=DocumentStatus.queued,
        created_by=user_id,
        metadata_={"active_content_warnings": warnings} if warnings else {},
        **fields,
    )
    if content_type == "application/pdf":
        try:
            doc.page_count = ex.pdf_page_count(upload_path)
        except Exception as exc:  # noqa: BLE001
            raise ValidationFailed(f"PDF could not be opened: {exc}", code="corrupt_pdf") from exc
    db.add(doc)
    db.flush()
    enqueue(db, PROCESS_JOB, {"document_id": str(doc.id)}, org_id=org_id, created_by=user_id, dedupe_key=f"process:{doc.id}")
    record(
        db,
        "document.uploaded",
        org_id=org_id,
        actor_id=user_id,
        entity_type="document",
        entity_id=doc.id,
        detail={"sha256": digest, "filename": filename, "content_type": content_type},
        request_id=request_id,
    )
    return doc, True


def request_processing(db: Session, doc: Document, user_id: uuid.UUID, *, force_ocr: bool = False):  # noqa: ANN201
    if doc.status in (DocumentStatus.processing,):
        raise Conflict("Document is already being processed")
    if doc.status == DocumentStatus.quarantined:
        raise Conflict("Document is quarantined after a failed malware scan and cannot be processed")
    doc.status = DocumentStatus.queued
    doc.processing_error = None
    return enqueue(
        db,
        PROCESS_JOB,
        {"document_id": str(doc.id), "force_ocr": force_ocr},
        org_id=doc.org_id,
        created_by=user_id,
        dedupe_key=f"process:{doc.id}",
    )


def _chunks(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            cut = text.rfind(" ", start + CHUNK_CHARS // 2, end)
            end = cut if cut > start else end
        spans.append((start, end))
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return spans


@handler(PROCESS_JOB)
def process_document(job: JobContext) -> dict:
    doc_id = uuid.UUID(job.payload["document_id"])
    force_ocr = bool(job.payload.get("force_ocr"))
    settings = get_settings()
    db = job.session()
    tmpdir = Path(tempfile.mkdtemp(prefix="cbi-proc-"))
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            raise PermanentJobError(f"Document {doc_id} not found")
        doc.status = DocumentStatus.processing
        db.commit()

        local = tmpdir / "original"
        with get_store().open(doc.storage_key) as src, local.open("wb") as out:
            shutil.copyfileobj(src, out)
        if sha256_file(local) != doc.sha256:
            raise PermanentJobError("Stored object checksum does not match the recorded SHA-256")

        scan, scan_detail = clamav_scan(local)
        doc.malware_scan = scan
        if scan == ScanResult.infected:
            doc.status = DocumentStatus.quarantined
            doc.processing_error = f"Malware detected: {scan_detail}"
            db.commit()
            raise PermanentJobError(doc.processing_error)
        if scan != ScanResult.clean and settings.require_malware_scan:
            db.commit()
            raise RuntimeError(f"Malware scanning required but {scan}: {scan_detail}")  # retryable
        db.commit()

        version = PIPELINE_VERSION
        # Replace any partial output from a previous attempt with the same version.
        db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id, DocumentPage.processing_version == version))
        db.flush()

        pages: list[DocumentPage] = []
        total = doc.page_count or 1
        for pt in ex.extract(local, doc.content_type, force_ocr=force_ocr):
            page = DocumentPage(
                org_id=doc.org_id,
                document_id=doc.id,
                page_number=pt.page_number,
                text=pt.text,
                normalised_text=normalise(pt.text),
                extraction_method=ExtractionMethod(pt.method),
                ocr_confidence=pt.ocr_confidence,
                detected_language=pt.language,
                processing_version=version,
                is_current=False,
                char_count=len(pt.text),
            )
            db.add(page)
            pages.append(page)
            job.progress(min(0.9, pt.page_number / max(total, 1) * 0.9), f"Extracted page {pt.page_number}")
        db.flush()

        # Swap the current version atomically.
        db.execute(
            update(DocumentPage)
            .where(DocumentPage.document_id == doc.id, DocumentPage.processing_version != version)
            .values(is_current=False)
        )
        for p in pages:
            p.is_current = True

        db.execute(delete(PageChunk).where(PageChunk.document_id == doc.id))
        for p in pages:
            for i, (s, e) in enumerate(_chunks(p.text)):
                db.add(
                    PageChunk(
                        org_id=doc.org_id,
                        page_id=p.id,
                        document_id=doc.id,
                        page_number=p.page_number,
                        chunk_index=i,
                        text=p.text[s:e],
                        char_start=s,
                        char_end=e,
                    )
                )

        # Unreviewed machine candidates from earlier runs are replaced; reviewed claims are preserved.
        db.execute(
            delete(Claim).where(
                Claim.document_id == doc.id,
                Claim.extraction_method == ExtractionMethod.rule,
                Claim.review_status == ReviewStatus.unreviewed,
            )
        )
        n_claims = 0
        for p in pages:
            for c in extract_candidates(p.text):
                db.add(
                    Claim(
                        org_id=doc.org_id,
                        document_id=doc.id,
                        page_number=p.page_number,
                        char_start=c.char_start,
                        char_end=c.char_end,
                        claim_type=ClaimType(c.claim_type),
                        statement=c.statement,
                        commodities=c.commodities,
                        attributes=c.attributes,
                        extraction_method=ExtractionMethod.rule,
                        extractor_version=RULES_VERSION,
                        confidence=c.confidence,
                    )
                )
                n_claims += 1

        langs = sorted({p.detected_language for p in pages if p.detected_language})
        ocr_pages = [p for p in pages if p.extraction_method == ExtractionMethod.ocr]
        empty_pages = [p.page_number for p in pages if p.char_count == 0]
        doc.page_count = len(pages)
        doc.language = doc.language or ("+".join(langs) if langs else None)
        doc.processing_version = version
        doc.processed_at = datetime.now(UTC)
        doc.status = DocumentStatus.processed
        doc.processing_error = None
        summary = {
            "pages": len(pages),
            "ocr_pages": len(ocr_pages),
            "empty_pages": empty_pages,
            "mean_ocr_confidence": round(sum(p.ocr_confidence or 0 for p in ocr_pages) / len(ocr_pages), 2) if ocr_pages else None,
            "claim_candidates": n_claims,
            "processing_version": version,
            "malware_scan": scan,
        }
        doc.metadata_ = {**(doc.metadata_ or {}), "last_processing": summary}
        record(db, "document.processed", org_id=doc.org_id, actor_id=None, entity_type="document", entity_id=doc.id, detail=summary)
        db.commit()
        return summary
    except Exception as exc:
        db.rollback()
        _mark_failed(db, doc_id, exc, terminal=isinstance(exc, PermanentJobError) or job.attempt >= job.max_attempts)
        raise
    finally:
        db.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _mark_failed(db: Session, doc_id: uuid.UUID, exc: Exception, *, terminal: bool) -> None:
    doc = db.get(Document, doc_id)
    if doc is None or doc.status == DocumentStatus.quarantined:
        return
    doc.status = DocumentStatus.failed if terminal else DocumentStatus.queued
    doc.processing_error = f"{type(exc).__name__}: {exc}"[:2000]
    db.commit()
