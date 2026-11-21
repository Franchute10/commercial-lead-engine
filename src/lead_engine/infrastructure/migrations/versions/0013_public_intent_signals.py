"""Persist captured public service-request signals.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intent_signals",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), sa.ForeignKey("campaigns.id"), index=True),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("request_type", sa.String(40), nullable=False),
        sa.Column("service_requested", sa.String(1000), nullable=False),
        sa.Column("evidence_excerpt", sa.String(1000), nullable=False),
        sa.Column("city", sa.String(1000), nullable=True),
        sa.Column("region", sa.String(1000), nullable=True),
        sa.Column("country", sa.String(1000), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intent_score", sa.Integer(), nullable=False),
        sa.Column("score_explanation", sa.String(2000), nullable=False),
        sa.Column("qualified_candidate", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "platform IN ('FACEBOOK_GROUP','REDDIT','NEXTDOOR','PUBLIC_WEB','OTHER')",
            name="intent_signal_platform",
        ),
        sa.CheckConstraint(
            "request_type IN ('SERVICE_REQUEST','QUOTE_REQUEST','RECOMMENDATION_REQUEST')",
            name="intent_signal_request_type",
        ),
        sa.CheckConstraint("intent_score >= 0 AND intent_score <= 100", name="ck_intent_signal_score"),
        sa.UniqueConstraint("source_url", name="uq_intent_signal_source_url"),
    )


def downgrade() -> None:
    op.drop_table("intent_signals")
