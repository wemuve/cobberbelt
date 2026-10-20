"""Sources, documents, pages, extracted claims, geological features and review decisions.

Design rule: every fact traces back to a page of an original document (or to a
named field/lab record) with its extraction method, processing version,
confidence and review status. Conflicting claims are linked, never merged.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from copperbelt.db import Base
from copperbelt.models.base import IdMixin, StrEnum, TenantMixin, TimestampMixin

# ---------------------------------------------------------------- enumerations


class AccessStatus(StrEnum):
    authorised = "authorised"  # written permission / licence on file
    open_licence = "open_licence"  # published under a licence permitting this use
    public_domain = "public_domain"
    pending = "pending"  # access requested, not yet granted
    not_authorised = "not_authorised"
    unknown = "unknown"  # not yet investigated: ingestion is blocked


INGESTIBLE_ACCESS = {AccessStatus.authorised, AccessStatus.open_licence, AccessStatus.public_domain}


class Classification(StrEnum):
    public = "public"
    institutional = "institutional"  # all members of the owning organisation
    project_restricted = "project_restricted"  # only members of linked projects (+ admins)
    confidential = "confidential"  # admins/owners and explicitly granted users


class DocumentStatus(StrEnum):
    uploaded = "uploaded"
    queued = "queued"
    processing = "processing"
    processed = "processed"
    failed = "failed"
    quarantined = "quarantined"  # failed malware scan


class ExtractionMethod(StrEnum):
    text_layer = "text_layer"
    ocr = "ocr"
    manual = "manual"
    rule = "rule"
    llm = "llm"
    imported = "imported"


class ReviewStatus(StrEnum):
    unreviewed = "unreviewed"
    accepted = "accepted"
    rejected = "rejected"
    needs_information = "needs_information"


class EvidenceBasis(StrEnum):
    """How the underlying observation was made — independent of our review of the extraction."""

    historical_reported = "historical_reported"
    independently_verified = "independently_verified"
    field_observation = "field_observation"
    laboratory_result = "laboratory_result"
    remote_sensing = "remote_sensing"
    unknown = "unknown"


class ClaimType(StrEnum):
    mineral_occurrence = "mineral_occurrence"
    mineralisation = "mineralisation"
    formation = "formation"
    exploration_activity = "exploration_activity"
    drilling_reference = "drilling_reference"
    sample_assay_reference = "sample_assay_reference"
    survey_location = "survey_location"
    coordinate = "coordinate"
    observation = "observation"
    other = "other"


class LocationPrecision(StrEnum):
    surveyed = "surveyed"  # stated with survey-grade precision
    reported_coordinates = "reported_coordinates"  # coordinates stated in source, precision as stated
    georeferenced_map = "georeferenced_map"  # digitised from a georeferenced map
    approximate = "approximate"  # place-name or sketch-map level
    district = "district"  # only an administrative area is known
    unknown = "unknown"


class EvidenceRelation(StrEnum):
    supports = "supports"
    contradicts = "contradicts"
    mentions = "mentions"


# ---------------------------------------------------------------- sources


class Source(IdMixin, TimestampMixin, TenantMixin, Base):
    """Where documents/data come from, and on what legal basis we hold them."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    institution: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    access_method: Mapped[str] = mapped_column(String(40), default="manual_upload", nullable=False)
    access_status: Mapped[AccessStatus] = mapped_column(
        Enum(AccessStatus, name="access_status"), default=AccessStatus.unknown, nullable=False
    )
    licence: Mapped[str | None] = mapped_column(String(200))
    terms_url: Mapped[str | None] = mapped_column(String(500))
    permits_commercial_use: Mapped[bool | None] = mapped_column()
    permits_redistribution: Mapped[bool | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


# ---------------------------------------------------------------- documents


class Document(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("org_id", "sha256", name="uq_documents_org_sha256"),)

    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sources.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    authors: Mapped[list[str]] = mapped_column(ARRAY(String(200)), default=list, nullable=False)
    publication_date: Mapped[date | None] = mapped_column(Date)
    publication_year: Mapped[int | None] = mapped_column(Integer)
    document_type: Mapped[str | None] = mapped_column(String(60))
    source_reference: Mapped[str | None] = mapped_column(String(300))  # e.g. archive call number, DOI
    language: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(2))
    classification: Mapped[Classification] = mapped_column(
        Enum(Classification, name="classification"), default=Classification.institutional, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"), default=DocumentStatus.uploaded, nullable=False
    )
    malware_scan: Mapped[str] = mapped_column(String(30), default="not_scanned", nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    processing_version: Mapped[str | None] = mapped_column(String(40))
    processing_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))

    source: Mapped[Source] = relationship(lazy="joined")


class DocumentAccessGrant(IdMixin, TimestampMixin, TenantMixin, Base):
    """Explicit per-user access to a confidential document."""

    __tablename__ = "document_access_grants"
    __table_args__ = (UniqueConstraint("document_id", "user_id"),)

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class DocumentPage(IdMixin, TenantMixin, Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", "processing_version"),
        Index("ix_pages_tsv", "tsv", postgresql_using="gin"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based, as printed by the viewer
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    normalised_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        Enum(ExtractionMethod, name="extraction_method"), nullable=False
    )
    ocr_confidence: Mapped[float | None] = mapped_column(Float)  # 0-100 mean word confidence (Tesseract)
    detected_language: Mapped[str | None] = mapped_column(String(20))
    processing_version: Mapped[str] = mapped_column(String(40), nullable=False)
    is_current: Mapped[bool] = mapped_column(default=True, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tsv = deferred(
        mapped_column(
            TSVECTOR,
            Computed(
                "setweight(to_tsvector('english', coalesce(normalised_text,'')), 'A') || "
                "setweight(to_tsvector('french', coalesce(text,'')), 'B') || "
                "to_tsvector('simple', coalesce(text,''))",
                persisted=True,
            ),
        )
    )


class PageChunk(IdMixin, TenantMixin, Base):
    """Retrieval unit for semantic search. Embedding is null until an embedding provider runs."""

    __tablename__ = "page_chunks"

    page_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding = deferred(mapped_column(Vector(1536), nullable=True))
    embedding_model: Mapped[str | None] = mapped_column(String(100))


# ---------------------------------------------------------------- claims & features


class Claim(IdMixin, TimestampMixin, TenantMixin, Base):
    """A single statement found in a source, with exact provenance."""

    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_claim_confidence"),
        CheckConstraint(
            "(document_id IS NULL) = (page_number IS NULL)", name="ck_claim_page_with_document"
        ),
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id"), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    claim_type: Mapped[ClaimType] = mapped_column(Enum(ClaimType, name="claim_type"), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)  # verbatim excerpt from the source
    summary: Mapped[str | None] = mapped_column(Text)  # optional normalised paraphrase
    commodities: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    reported_date: Mapped[date | None] = mapped_column(Date)  # date the observation was made/reported
    evidence_basis: Mapped[EvidenceBasis] = mapped_column(
        Enum(EvidenceBasis, name="evidence_basis"), default=EvidenceBasis.historical_reported, nullable=False
    )
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        Enum(ExtractionMethod, name="extraction_method", create_type=False), nullable=False
    )
    extractor_version: Mapped[str] = mapped_column(String(60), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), default=ReviewStatus.unreviewed, nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class GeoFeature(IdMixin, TimestampMixin, TenantMixin, Base):
    """A geological feature or location of interest stored in EPSG:4326."""

    __tablename__ = "geo_features"
    __table_args__ = (
        Index("ix_geo_features_geom", "geom", postgresql_using="gist"),
        CheckConstraint("uncertainty_m IS NULL OR uncertainty_m >= 0", name="ck_feature_uncertainty"),
    )

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    feature_type: Mapped[str] = mapped_column(String(60), nullable=False)  # mineral_occurrence, prospect, borehole, formation...
    commodities: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=True)
    source_crs: Mapped[str | None] = mapped_column(String(40))  # e.g. "EPSG:32735" as supplied
    original_coordinates: Mapped[str | None] = mapped_column(Text)  # verbatim as written in the source
    location_precision: Mapped[LocationPrecision] = mapped_column(
        Enum(LocationPrecision, name="location_precision"), default=LocationPrecision.unknown, nullable=False
    )
    uncertainty_m: Mapped[float | None] = mapped_column(Float)
    country: Mapped[str | None] = mapped_column(String(2))
    admin_area: Mapped[str | None] = mapped_column(String(200))  # province / district as stated
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status", create_type=False), default=ReviewStatus.unreviewed, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeatureEvidence(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "feature_evidence"
    __table_args__ = (UniqueConstraint("feature_id", "claim_id"),)

    feature_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("geo_features.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True)
    relation: Mapped[EvidenceRelation] = mapped_column(Enum(EvidenceRelation, name="evidence_relation"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class ReviewDecision(IdMixin, TenantMixin, Base):
    """Immutable history of review decisions on claims and features."""

    __tablename__ = "review_decisions"

    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    previous_status: Mapped[str] = mapped_column(String(40), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
