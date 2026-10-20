from copperbelt.models.evidence import (  # noqa: F401
    CatalogRecord,
    Claim,
    Document,
    DocumentAccessGrant,
    DocumentPage,
    FeatureEvidence,
    GeoFeature,
    PageChunk,
    ReviewDecision,
    Source,
)
from copperbelt.models.identity import Membership, Organisation, Role, User  # noqa: F401
from copperbelt.models.ops import (  # noqa: F401
    AuditEvent,
    IdempotencyRecord,
    Job,
    JobStatus,
    ProviderCheck,
    ProviderUsage,
)
from copperbelt.models.projects import (  # noqa: F401
    AreaOfInterest,
    Assessment,
    EvidenceGap,
    Project,
    ProjectDocument,
    ProjectFeature,
    ProjectMember,
    Risk,
)

TENANT_TABLES = [
    "sources",
    "catalog_records",
    "documents",
    "document_access_grants",
    "document_pages",
    "page_chunks",
    "claims",
    "geo_features",
    "feature_evidence",
    "review_decisions",
    "projects",
    "project_members",
    "areas_of_interest",
    "project_documents",
    "project_features",
    "risks",
    "evidence_gaps",
    "assessments",
    "idempotency_records",
]
