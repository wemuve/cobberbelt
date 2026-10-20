"""Project workspaces, areas of interest, risks, evidence gaps and versioned assessments."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from copperbelt.db import Base
from copperbelt.models.base import IdMixin, StrEnum, TenantMixin, TimestampMixin


class ProjectRole(StrEnum):
    lead = "lead"
    contributor = "contributor"
    reader = "reader"


class Project(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    commodities: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), default="screening", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMember(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[ProjectRole] = mapped_column(Enum(ProjectRole, name="project_role"), nullable=False)


class AreaOfInterest(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "areas_of_interest"
    __table_args__ = (Index("ix_aoi_geom", "geom", postgresql_using="gist"),)

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    geom = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    source_crs: Mapped[str | None] = mapped_column(String(40))
    area_km2: Mapped[float | None] = mapped_column()
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class ProjectDocument(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "project_documents"
    __table_args__ = (UniqueConstraint("project_id", "document_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)


class ProjectFeature(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "project_features"
    __table_args__ = (UniqueConstraint("project_id", "feature_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("geo_features.id", ondelete="CASCADE"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class RiskStatus(StrEnum):
    open = "open"
    mitigating = "mitigating"
    closed = "closed"
    accepted = "accepted"


class Risk(IdMixin, TimestampMixin, TenantMixin, Base):
    """A project risk tied to evidence and to the action that would resolve it."""

    __tablename__ = "risks"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # geological, title, environmental, infrastructure, financial, social, data_quality
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5, defined in docs/scoring.md
    likelihood: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5
    claim_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list, nullable=False)
    verification_action: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[RiskStatus] = mapped_column(Enum(RiskStatus, name="risk_status"), default=RiskStatus.open, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class EvidenceGap(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "evidence_gaps"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    why_it_matters: Mapped[str | None] = mapped_column(Text)
    verification_action: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open", nullable=False)  # open, in_progress, resolved
    resolved_by_claim_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("claims.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class AssessmentStatus(StrEnum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    superseded = "superseded"


class Assessment(IdMixin, TimestampMixin, TenantMixin, Base):
    """A frozen, hash-sealed snapshot of a project's evidence, risks and gaps.

    The ``snapshot`` is generated by the server from current records; the
    ``content_sha256`` lets anyone verify a report has not been altered.
    """

    __tablename__ = "assessments"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[AssessmentStatus] = mapped_column(
        Enum(AssessmentStatus, name="assessment_status"), default=AssessmentStatus.draft, nullable=False
    )
    method_version: Mapped[str] = mapped_column(String(40), nullable=False)
    reviewer_conclusion: Mapped[str | None] = mapped_column(Text)  # human professional judgement, labelled as such
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
