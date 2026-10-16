"""Persist human-reviewed sector suggestions.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sector_suggestions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("requested_name", sa.String(1000), nullable=False),
        sa.Column("suggested_sector_id", sa.String(1000), nullable=True),
        sa.Column("suggested_subsector_id", sa.String(1000), nullable=True),
        sa.Column("suggested_profile_id", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("review_note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(requested_name)) > 0", name="ck_sector_suggestion_name"),
        sa.CheckConstraint(
            "status IN ('PROVISIONAL','NEEDS_REVIEW','ACCEPTED','REJECTED')",
            name="ck_sector_suggestion_status",
        ),
        sa.CheckConstraint(
            "(status IN ('ACCEPTED','REJECTED') AND reviewed_at IS NOT NULL) OR "
            "(status IN ('PROVISIONAL','NEEDS_REVIEW') AND reviewed_at IS NULL)",
            name="ck_sector_suggestion_reviewed_at",
        ),
    )


def downgrade() -> None:
    op.drop_table("sector_suggestions")
