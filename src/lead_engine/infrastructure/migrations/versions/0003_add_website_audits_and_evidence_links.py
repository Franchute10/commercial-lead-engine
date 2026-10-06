"""add website audits and evidence links
Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import lead_engine.infrastructure.types

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "website_audits",
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("website_url", sa.String(length=1000), nullable=True),
        sa.Column("final_url", sa.String(length=1000), nullable=True),
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
                "PENDING",
                "SUCCESS",
                "PARTIAL",
                "FAILED",
                "NO_WEBSITE",
                name="website_audit_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Float(), nullable=True),
        sa.Column("body_size_bytes", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=1000), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_website_audits_company_id"), "website_audits", ["company_id"], unique=False
    )
    # SQLite supports a nullable inline REFERENCES column without rebuilding evidence.
    # Avoid batch table recreation, which would disturb existing score evidence foreign keys.
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE evidence ADD COLUMN website_audit_id CHAR(32) "
            "REFERENCES website_audits (id)"
        )
    else:
        op.add_column("evidence", sa.Column("website_audit_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_evidence_website_audit", "evidence", "website_audits", ["website_audit_id"], ["id"]
        )
    op.create_index("ix_evidence_website_audit_id", "evidence", ["website_audit_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_evidence_website_audit_id", table_name="evidence")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE evidence DROP COLUMN website_audit_id")
    else:
        op.drop_constraint("fk_evidence_website_audit", "evidence", type_="foreignkey")
        op.drop_column("evidence", "website_audit_id")
    op.drop_index("ix_website_audits_company_id", table_name="website_audits")
    op.drop_table("website_audits")
