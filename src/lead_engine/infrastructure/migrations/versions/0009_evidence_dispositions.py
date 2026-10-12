"""Append-only evidence retractions and supersessions.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from lead_engine.infrastructure.types import UTCDateTime

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_dispositions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("evidence_id", sa.Uuid(), sa.ForeignKey("evidence.id"), nullable=False),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "replacement_evidence_id", sa.Uuid(), sa.ForeignKey("evidence.id"), nullable=True
        ),
        sa.Column("actor", sa.String(1000), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("recorded_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("evidence_id", name="uq_evidence_disposition_once"),
        sa.CheckConstraint(
            "(kind = 'RETRACTED' AND replacement_evidence_id IS NULL) OR "
            "(kind = 'SUPERSEDED' AND replacement_evidence_id IS NOT NULL)",
            name="ck_evidence_disposition_replacement",
        ),
        sa.CheckConstraint(
            "replacement_evidence_id IS NULL OR replacement_evidence_id != evidence_id",
            name="ck_evidence_disposition_not_self",
        ),
    )
    op.create_index(
        "ix_evidence_dispositions_evidence_id", "evidence_dispositions", ["evidence_id"]
    )
    op.create_index("ix_evidence_dispositions_company_id", "evidence_dispositions", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_dispositions_company_id", table_name="evidence_dispositions")
    op.drop_index("ix_evidence_dispositions_evidence_id", table_name="evidence_dispositions")
    op.drop_table("evidence_dispositions")
