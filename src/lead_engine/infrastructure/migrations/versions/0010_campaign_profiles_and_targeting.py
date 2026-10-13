"""Persist campaign profiles and structured targeting.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("profile_id", sa.String(1000), nullable=True))
    op.add_column("campaigns", sa.Column("country", sa.String(1000), nullable=True))
    op.add_column("campaigns", sa.Column("region", sa.String(1000), nullable=True))
    op.add_column("campaigns", sa.Column("city", sa.String(1000), nullable=True))
    op.add_column("campaigns", sa.Column("industry", sa.String(1000), nullable=True))
    op.add_column("campaigns", sa.Column("subindustry", sa.String(1000), nullable=True))
    op.execute(
        sa.text(
            "UPDATE campaigns SET profile_id = CASE campaign_type "
            "WHEN 'HEALTH' THEN 'health-v1' "
            "WHEN 'CONSTRUCTION' THEN 'construction-v1' "
            "WHEN 'HOSPITALITY' THEN 'hospitality-v1' END"
        )
    )
    # Keep the column nullable in SQLite: rebuilding campaigns to add NOT NULL can
    # invalidate existing foreign keys from leads. Campaign validates a resolved profile
    # before every application persistence operation, and this migration backfills all rows.


def downgrade() -> None:
    op.drop_column("campaigns", "subindustry")
    op.drop_column("campaigns", "industry")
    op.drop_column("campaigns", "city")
    op.drop_column("campaigns", "region")
    op.drop_column("campaigns", "country")
    op.drop_column("campaigns", "profile_id")
