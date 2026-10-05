"""add discovery run audit trail
Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import lead_engine.infrastructure.types

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_runs",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=1000), nullable=False),
        sa.Column("query", sa.JSON(), nullable=False),
        sa.Column(
            "started_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "RUNNING",
                "COMPLETED",
                "PARTIAL",
                "FAILED",
                name="discovery_run_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("outcomes", sa.JSON(), nullable=False),
        sa.Column("provider_error", sa.String(length=4000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_discovery_runs_campaign_id"), "discovery_runs", ["campaign_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_discovery_runs_campaign_id"), table_name="discovery_runs")
    op.drop_table("discovery_runs")
