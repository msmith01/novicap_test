"""Initial schema: applications and append-only score_history.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_application_id", sa.String, nullable=False),
        sa.Column("employees", sa.Integer, nullable=False),
        sa.Column("annual_revenue", sa.Float, nullable=False),
        sa.Column("requested_amount", sa.Float, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_applications_source_application_id", "applications", ["source_application_id"])
    op.create_table(
        "score_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False, unique=True),
        sa.Column("decision", sa.String, nullable=False),
        sa.Column("risk_factors", sa.JSON, nullable=False),
        sa.Column("ruleset_version", sa.String, nullable=False),
        sa.Column("image_version", sa.String, nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_score_history_application_id", "score_history", ["application_id"])


def downgrade() -> None:
    op.drop_table("score_history")
    op.drop_table("applications")
