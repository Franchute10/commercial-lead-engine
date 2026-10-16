"""Add optional sector classification to campaigns.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("sector_id", sa.String(1000), nullable=True))
    op.add_column("campaigns", sa.Column("subsector_id", sa.String(1000), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "subsector_id")
    op.drop_column("campaigns", "sector_id")
