"""Catalogue harvest: per-source connector config and catalogue records (metadata only).

Revision ID: 0004
Revises: 0003
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("CBI_APP_DB_ROLE", "copperbelt_app")
PREDICATE = (
    "current_setting('app.system', true) = 'on' "
    "OR org_id::text = nullif(current_setting('app.org_id', true), '')"
)


def upgrade() -> None:
    op.create_table('catalog_records',
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('external_id', sa.String(length=200), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('authors', postgresql.ARRAY(sa.String(length=300)), nullable=False),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('language', sa.String(length=20), nullable=True),
    sa.Column('document_type', sa.String(length=100), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('landing_url', sa.String(length=1000), nullable=True),
    sa.Column('file_url', sa.String(length=1000), nullable=True),
    sa.Column('rights_statement', sa.Text(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('changed_upstream_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('document_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id', name='uq_catalog_source_external')
    )
    op.create_index(op.f('ix_catalog_records_org_id'), 'catalog_records', ['org_id'], unique=False)
    op.create_index(op.f('ix_catalog_records_source_id'), 'catalog_records', ['source_id'], unique=False)
    op.add_column('sources', sa.Column('connector_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON catalog_records TO {APP_ROLE}")
    op.execute("ALTER TABLE catalog_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE catalog_records FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON catalog_records USING ({PREDICATE}) WITH CHECK ({PREDICATE})")
    op.execute("CREATE INDEX ix_catalog_records_title_trgm ON catalog_records USING gin (title gin_trgm_ops)")


def downgrade() -> None:
    op.drop_table("catalog_records")
    op.drop_column("sources", "connector_config")
