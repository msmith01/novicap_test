"""Add nullable applications.source_system (backward compatible).

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("source_system", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "source_system")
