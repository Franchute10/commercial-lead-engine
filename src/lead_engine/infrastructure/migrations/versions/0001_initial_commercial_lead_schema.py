"""initial commercial lead schema
Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import lead_engine.infrastructure.types

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=1000), nullable=False),
        sa.Column(
            "campaign_type",
            sa.Enum(
                "HEALTH",
                "CONSTRUCTION",
                "HOSPITALITY",
                name="campaigns_campaign_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("geography", sa.String(length=1000), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(length=1000), nullable=False),
        sa.Column("legal_name", sa.String(length=1000), nullable=True),
        sa.Column("website", sa.String(length=2048), nullable=True),
        sa.Column("primary_domain", sa.String(length=1000), nullable=True),
        sa.Column("industry", sa.String(length=1000), nullable=True),
        sa.Column("subindustry", sa.String(length=1000), nullable=True),
        sa.Column("city", sa.String(length=1000), nullable=True),
        sa.Column("region", sa.String(length=1000), nullable=True),
        sa.Column("country", sa.String(length=1000), nullable=True),
        sa.Column("address", sa.String(length=1000), nullable=True),
        sa.Column("phone", sa.String(length=1000), nullable=True),
        sa.Column("email", sa.String(length=1000), nullable=True),
        sa.Column("instagram_url", sa.String(length=2048), nullable=True),
        sa.Column("facebook_url", sa.String(length=2048), nullable=True),
        sa.Column("linkedin_url", sa.String(length=2048), nullable=True),
        sa.Column("google_maps_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(
                "WEBSITE",
                "GOOGLE_SEARCH",
                "GOOGLE_MAPS",
                "LINKEDIN_PUBLIC",
                "FACEBOOK_PUBLIC",
                "INSTAGRAM_PUBLIC",
                "GOVERNMENT_REGISTRY",
                "BUSINESS_DIRECTORY",
                "MANUAL",
                "OTHER",
                name="sources_source_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=1000), nullable=True),
        sa.Column(
            "retrieved_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("source_metadata", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "company_identities",
        sa.Column("key", sa.String(length=2048), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        op.f("ix_company_identities_company_id"), "company_identities", ["company_id"], unique=False
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(length=1000), nullable=False),
        sa.Column("role_title", sa.String(length=1000), nullable=True),
        sa.Column(
            "role_category",
            sa.Enum(
                "OWNER",
                "FOUNDER",
                "GENERAL_MANAGEMENT",
                "COMMERCIAL",
                "MARKETING",
                "DIGITAL",
                "CUSTOMER_EXPERIENCE",
                "MEDICAL_DIRECTOR",
                "ADMINISTRATION",
                "UNKNOWN",
                name="contacts_role_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("linkedin_url", sa.String(length=2048), nullable=True),
        sa.Column("email", sa.String(length=1000), nullable=True),
        sa.Column("phone", sa.String(length=1000), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_contacts_confidence"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contacts_company_id"), "contacts", ["company_id"], unique=False)
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_type", sa.String(length=1000), nullable=False),
        sa.Column("statement", sa.String(length=1000), nullable=False),
        sa.Column("raw_value", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "observed_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_evidence_confidence"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evidence_company_id"), "evidence", ["company_id"], unique=False)
    op.create_index(op.f("ix_evidence_source_id"), "evidence", ["source_id"], unique=False)
    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DISCOVERED",
                "QUALIFYING",
                "QUALIFIED",
                "REJECTED",
                "READY_FOR_RESEARCH",
                "READY_FOR_OUTREACH",
                "CONTACTED",
                "RESPONDED",
                "MEETING",
                "PROPOSAL",
                "WON",
                "LOST",
                "ARCHIVED",
                name="leads_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint("priority >= 0 AND priority <= 100", name="ck_lead_priority"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "campaign_id", name="uq_lead_company_campaign"),
    )
    op.create_index(op.f("ix_leads_campaign_id"), "leads", ["campaign_id"], unique=False)
    op.create_index(op.f("ix_leads_company_id"), "leads", ["company_id"], unique=False)
    op.create_table(
        "lead_interactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column(
            "interaction_type",
            sa.Enum(
                "LINKEDIN_CONNECTION",
                "LINKEDIN_MESSAGE",
                "EMAIL",
                "WHATSAPP",
                "PHONE_CALL",
                "MEETING",
                "FOLLOW_UP",
                "PROPOSAL_SENT",
                "NOTE",
                name="lead_interactions_interaction_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=1000), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_lead_interactions_contact_id"), "lead_interactions", ["contact_id"], unique=False
    )
    op.create_index(
        op.f("ix_lead_interactions_lead_id"), "lead_interactions", ["lead_id"], unique=False
    )
    op.create_table(
        "lead_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("total_score", sa.Numeric(precision=7, scale=4), nullable=False),
        sa.Column("scoring_version", sa.String(length=1000), nullable=False),
        sa.Column(
            "calculated_at",
            lead_engine.infrastructure.types.UTCDateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("explanation", sa.String(length=1000), nullable=True),
        sa.CheckConstraint("total_score >= 0 AND total_score <= 100", name="ck_score_total"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lead_scores_lead_id"), "lead_scores", ["lead_id"], unique=False)
    op.create_table(
        "score_components",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_score_id", sa.Uuid(), nullable=False),
        sa.Column("criterion", sa.String(length=1000), nullable=False),
        sa.Column("points_awarded", sa.Numeric(precision=7, scale=4), nullable=False),
        sa.Column("max_points", sa.Numeric(precision=7, scale=4), nullable=False),
        sa.Column("explanation", sa.String(length=1000), nullable=False),
        sa.CheckConstraint(
            "points_awarded >= 0 AND max_points > 0 AND max_points <= 100 "
            "AND points_awarded <= max_points",
            name="ck_component_points",
        ),
        sa.ForeignKeyConstraint(["lead_score_id"], ["lead_scores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_score_id", "criterion", name="uq_score_criterion"),
    )
    op.create_index(
        op.f("ix_score_components_lead_score_id"),
        "score_components",
        ["lead_score_id"],
        unique=False,
    )
    op.create_table(
        "component_evidence",
        sa.Column("component_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["component_id"], ["score_components.id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"]),
        sa.PrimaryKeyConstraint("component_id", "evidence_id"),
    )


def downgrade() -> None:
    op.drop_table("component_evidence")
    op.drop_index(op.f("ix_score_components_lead_score_id"), table_name="score_components")
    op.drop_table("score_components")
    op.drop_index(op.f("ix_lead_scores_lead_id"), table_name="lead_scores")
    op.drop_table("lead_scores")
    op.drop_index(op.f("ix_lead_interactions_lead_id"), table_name="lead_interactions")
    op.drop_index(op.f("ix_lead_interactions_contact_id"), table_name="lead_interactions")
    op.drop_table("lead_interactions")
    op.drop_index(op.f("ix_leads_company_id"), table_name="leads")
    op.drop_index(op.f("ix_leads_campaign_id"), table_name="leads")
    op.drop_table("leads")
    op.drop_index(op.f("ix_evidence_source_id"), table_name="evidence")
    op.drop_index(op.f("ix_evidence_company_id"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_index(op.f("ix_contacts_company_id"), table_name="contacts")
    op.drop_table("contacts")
    op.drop_index(op.f("ix_company_identities_company_id"), table_name="company_identities")
    op.drop_table("company_identities")
    op.drop_table("sources")
    op.drop_table("companies")
    op.drop_table("campaigns")
