"""PostgreSQL extensions.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Extensions are left in place; other databases objects may depend on them.
    pass
