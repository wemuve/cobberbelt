"""Row-level security, application-role grants and append-only audit log.

The application role (default ``copperbelt_app``) must exist before this runs;
``deploy/db/bootstrap.sql`` creates it. It is deliberately NOT a superuser and
does NOT own the tables, so RLS policies apply to it.

Revision ID: 0003
Revises: 0002
"""

import os

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("CBI_APP_DB_ROLE", "copperbelt_app")

TENANT_TABLES = [
    "sources", "documents", "document_access_grants", "document_pages", "page_chunks", "claims",
    "geo_features", "feature_evidence", "review_decisions", "projects", "project_members",
    "areas_of_interest", "project_documents", "project_features", "risks", "evidence_gaps",
    "assessments", "idempotency_records",
]
# Tables whose org_id may be NULL for platform-level rows.
NULLABLE_TENANT_TABLES = ["audit_events", "jobs", "provider_usage"]

TENANT_PREDICATE = (
    "current_setting('app.system', true) = 'on' "
    "OR org_id::text = nullif(current_setting('app.org_id', true), '')"
)


def upgrade() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")

    for table in TENANT_TABLES + NULLABLE_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({TENANT_PREDICATE}) "
            f"WITH CHECK ({TENANT_PREDICATE})"
        )

    # Audit log is append-only for the application.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_events FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON review_decisions FROM {APP_ROLE}")

    # Approved assessments are immutable at the database level.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_approved_assessment_change() RETURNS trigger AS $$
        BEGIN
          IF OLD.status = 'approved' AND (
               NEW.snapshot IS DISTINCT FROM OLD.snapshot
            OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
            OR NEW.reviewer_conclusion IS DISTINCT FROM OLD.reviewer_conclusion
            OR NEW.status NOT IN ('approved', 'superseded')) THEN
            RAISE EXCEPTION 'approved assessment % is immutable', OLD.id;
          END IF;
          IF TG_OP = 'DELETE' AND OLD.status IN ('approved', 'superseded') THEN
            RAISE EXCEPTION 'approved assessment % cannot be deleted', OLD.id;
          END IF;
          RETURN COALESCE(NEW, OLD);
        END $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_assessment_immutable BEFORE UPDATE OR DELETE ON assessments "
        "FOR EACH ROW EXECUTE FUNCTION prevent_approved_assessment_change()"
    )

    # Search helpers
    op.execute("CREATE INDEX ix_geo_features_name_trgm ON geo_features USING gin (name gin_trgm_ops)")
    op.execute("CREATE INDEX ix_documents_title_trgm ON documents USING gin (title gin_trgm_ops)")
    op.execute("CREATE INDEX ix_claims_commodities ON claims USING gin (commodities)")
    op.execute("CREATE INDEX ix_geo_features_commodities ON geo_features USING gin (commodities)")
    op.execute("CREATE INDEX ix_page_chunks_embedding ON page_chunks USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_page_chunks_embedding")
    op.execute("DROP INDEX IF EXISTS ix_geo_features_commodities")
    op.execute("DROP INDEX IF EXISTS ix_claims_commodities")
    op.execute("DROP INDEX IF EXISTS ix_documents_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_geo_features_name_trgm")
    op.execute("DROP TRIGGER IF EXISTS trg_assessment_immutable ON assessments")
    op.execute("DROP FUNCTION IF EXISTS prevent_approved_assessment_change()")
    for table in TENANT_TABLES + NULLABLE_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
