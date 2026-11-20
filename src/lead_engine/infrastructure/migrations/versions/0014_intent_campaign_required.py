"""Align intent campaign nullability without rewriting the deployed 0013 migration.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    missing = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM intent_signals WHERE campaign_id IS NULL")
    )
    if missing:
        raise ValueError(
            "Intent signals without a campaign exist. Assign their correct campaign "
            "before retrying migration 0014; no records have been deleted."
        )
    with op.batch_alter_table("intent_signals") as batch:
        batch.alter_column("campaign_id", existing_type=sa.Uuid(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("intent_signals") as batch:
        batch.alter_column("campaign_id", existing_type=sa.Uuid(), nullable=True)
