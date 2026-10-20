"""Request/response schemas for the v1 API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------ auth & orgs


class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=256)
    organisation_name: str = Field(min_length=2, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class MembershipOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    role: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    memberships: list[MembershipOut]


class MemberIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "analyst", "reviewer", "viewer"]
    initial_password: str = Field(min_length=12, max_length=256)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool


class RoleChangeIn(BaseModel):
    role: Literal["admin", "analyst", "reviewer", "viewer"]


# ------------------------------------------------------------------ sources


class SourceIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    institution: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    access_method: Literal["manual_upload", "api", "bulk_download", "institutional_transfer", "gis_service"] = "manual_upload"
    access_status: Literal["authorised", "open_licence", "public_domain", "pending", "not_authorised", "unknown"] = "unknown"
    licence: str | None = None
    terms_url: str | None = None
    permits_commercial_use: bool | None = None
    permits_redistribution: bool | None = None
    notes: str | None = None


class SourceOut(ORM, SourceIn):
    id: uuid.UUID
    created_at: datetime


# ------------------------------------------------------------------ documents


class DocumentOut(ORM):
    id: uuid.UUID
    source_id: uuid.UUID
    title: str
    authors: list[str]
    publication_year: int | None
    publication_date: date | None
    document_type: str | None
    source_reference: str | None
    language: str | None
    country: str | None
    classification: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: str
    malware_scan: str
    page_count: int | None
    processing_version: str | None
    processing_error: str | None
    processed_at: datetime | None
    metadata_: dict[str, Any] = Field(serialization_alias="metadata")
    version: int
    created_at: datetime


class DocumentUploadOut(BaseModel):
    document: DocumentOut
    created: bool
    job_id: uuid.UUID | None


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    authors: list[str] | None = None
    publication_year: int | None = Field(default=None, ge=1800, le=2100)
    document_type: str | None = None
    source_reference: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    classification: Literal["public", "institutional", "project_restricted", "confidential"] | None = None


class PageOut(ORM):
    page_number: int
    text: str
    extraction_method: str
    ocr_confidence: float | None
    detected_language: str | None
    processing_version: str
    char_count: int


class PageSummary(ORM):
    page_number: int
    extraction_method: str
    ocr_confidence: float | None
    char_count: int


class ProcessIn(BaseModel):
    force_ocr: bool = False


class JobOut(ORM):
    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    max_attempts: int
    progress: float
    progress_message: str | None
    last_error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


# ------------------------------------------------------------------ claims & features


class ClaimIn(BaseModel):
    document_id: uuid.UUID | None = None
    page_number: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    claim_type: str
    statement: str = Field(min_length=3, max_length=10000)
    summary: str | None = None
    commodities: list[str] = []
    attributes: dict[str, Any] = {}
    reported_date: date | None = None
    evidence_basis: Literal[
        "historical_reported", "independently_verified", "field_observation", "laboratory_result", "remote_sensing", "unknown"
    ] = "historical_reported"
    confidence: float | None = Field(default=None, ge=0, le=1)


class ClaimOut(ORM):
    id: uuid.UUID
    document_id: uuid.UUID | None
    page_number: int | None
    char_start: int | None
    char_end: int | None
    claim_type: str
    statement: str
    summary: str | None
    commodities: list[str]
    attributes: dict[str, Any]
    reported_date: date | None
    evidence_basis: str
    extraction_method: str
    extractor_version: str
    confidence: float | None
    review_status: str
    created_at: datetime


class ReviewIn(BaseModel):
    decision: Literal["accepted", "rejected", "needs_information"]
    comment: str | None = Field(default=None, max_length=5000)


class GeometryIn(BaseModel):
    type: str
    coordinates: Any

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        allowed = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
        if v not in allowed:
            raise ValueError(f"geometry type must be one of {sorted(allowed)}")
        return v


class FeatureIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    feature_type: Literal[
        "mineral_occurrence",
        "prospect",
        "deposit",
        "borehole",
        "sample_site",
        "survey_location",
        "formation_outcrop",
        "geological_boundary",
        "mine_working",
        "other",
    ]
    commodities: list[str] = []
    description: str | None = None
    geometry: GeometryIn | None = None
    source_crs: str | None = Field(default=None, description="e.g. 'EPSG:32735'. Defaults to EPSG:4326.")
    original_coordinates: str | None = None
    location_precision: Literal["surveyed", "reported_coordinates", "georeferenced_map", "approximate", "district", "unknown"] = "unknown"
    uncertainty_m: float | None = Field(default=None, ge=0)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    admin_area: str | None = None


class FeatureOut(BaseModel):
    id: uuid.UUID
    name: str
    feature_type: str
    commodities: list[str]
    description: str | None
    geometry: dict[str, Any] | None
    source_crs: str | None
    original_coordinates: str | None
    location_precision: str
    uncertainty_m: float | None
    country: str | None
    admin_area: str | None
    review_status: str
    evidence_counts: dict[str, int] = {}
    warnings: list[str] = []
    created_at: datetime


class FeaturePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    commodities: list[str] | None = None
    uncertainty_m: float | None = Field(default=None, ge=0, description="Set explicitly to null to mark uncertainty unknown")
    location_precision: Literal["surveyed", "reported_coordinates", "georeferenced_map", "approximate", "district", "unknown"] | None = None
    admin_area: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)


class EvidenceLinkIn(BaseModel):
    claim_id: uuid.UUID
    relation: Literal["supports", "contradicts", "mentions"]
    note: str | None = None


class PromoteClaimIn(BaseModel):
    """Create a feature from a reviewed coordinate claim."""

    name: str = Field(min_length=1, max_length=300)
    feature_type: str = "mineral_occurrence"
    commodities: list[str] = []
    datum_note: str | None = None
    uncertainty_m: float | None = Field(default=None, ge=0)
    lat_hemisphere: Literal["N", "S"] | None = Field(default=None, description="Required when the source does not state it")
    lon_hemisphere: Literal["E", "W"] | None = None
    hemisphere_basis: str | None = Field(default=None, max_length=1000, description="Why the reviewer chose these hemispheres")


# ------------------------------------------------------------------ search & geo


class SearchIn(BaseModel):
    q: str | None = Field(default=None, max_length=500)
    commodities: list[str] = []
    country: str | None = None
    document_type: str | None = None
    source_id: uuid.UUID | None = None
    year_from: int | None = None
    year_to: int | None = None
    review_status: list[str] = []
    geometry: GeometryIn | None = None
    bbox: tuple[float, float, float, float] | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class IntersectionIn(BaseModel):
    geometry: GeometryIn
    source_crs: str | None = None
    buffer_m: float = Field(default=0, ge=0, le=200_000)
    layers: list[Literal["features", "areas_of_interest"]] = ["features", "areas_of_interest"]


class AOIIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    project_id: uuid.UUID | None = None
    geometry: GeometryIn
    source_crs: str | None = None


class AOIOut(BaseModel):
    id: uuid.UUID
    name: str
    project_id: uuid.UUID | None
    geometry: dict[str, Any]
    source_crs: str | None
    area_km2: float | None
    warnings: list[str] = []
    created_at: datetime


# ------------------------------------------------------------------ projects


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    commodities: list[str] = []
    stage: Literal["screening", "due_diligence", "exploration", "development", "monitoring"] = "screening"


class ProjectOut(ORM, ProjectIn):
    id: uuid.UUID
    created_at: datetime
    my_role: str | None = None


class ProjectMemberIn(BaseModel):
    user_id: uuid.UUID
    role: Literal["lead", "contributor", "reader"]


class LinkIdsIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class RiskIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    category: Literal[
        "geological", "title", "environmental", "social", "infrastructure", "financial", "data_quality", "regulatory", "other"
    ]
    description: str | None = None
    severity: int = Field(ge=1, le=5)
    likelihood: int = Field(ge=1, le=5)
    claim_ids: list[uuid.UUID] = []
    verification_action: str | None = None
    owner_id: uuid.UUID | None = None
    due_date: date | None = None
    status: Literal["open", "mitigating", "closed", "accepted"] = "open"


class RiskOut(ORM, RiskIn):
    id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime


class GapIn(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    why_it_matters: str | None = None
    verification_action: str = Field(min_length=3)
    status: Literal["open", "in_progress", "resolved"] = "open"
    resolved_by_claim_id: uuid.UUID | None = None


class GapOut(ORM, GapIn):
    id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime


class AssessmentIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    reviewer_conclusion: str | None = Field(default=None, max_length=20000)


class AssessmentStatusIn(BaseModel):
    status: Literal["in_review", "approved"]
    reviewer_conclusion: str | None = Field(default=None, max_length=20000)


class AssessmentOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    title: str
    status: str
    method_version: str
    reviewer_conclusion: str | None
    content_sha256: str
    created_at: datetime
    approved_at: datetime | None
    snapshot: dict[str, Any] | None = None
