"""Relational mappings only; business rules live in domain/application."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from lead_engine.domain.audit import AuditStatus
from lead_engine.domain.discovery import DiscoveryStatus
from lead_engine.domain.enums import (
    CampaignType,
    InteractionType,
    LeadStatus,
    RoleCategory,
    SourceType,
)
from lead_engine.infrastructure.types import UTCDateTime


class Base(DeclarativeBase):
    pass


class EntityRow(Base):
    __abstract__ = True
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)


class CompanyRow(EntityRow):
    __tablename__ = "companies"
    canonical_name: Mapped[str] = mapped_column(String(1000))
    legal_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    primary_domain: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    subindustry: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    city: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    region: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    country: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    email: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    facebook_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    google_maps_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ContactRow(EntityRow):
    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_contacts_confidence"),
    )
    company_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(1000))
    role_title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    role_category: Mapped[RoleCategory] = mapped_column(
        Enum(RoleCategory, native_enum=False, create_constraint=True, name="contacts_role_category")
    )
    linkedin_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    email: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SourceRow(EntityRow):
    __tablename__ = "sources"
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, create_constraint=True, name="sources_source_type")
    )
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime())
    source_metadata: Mapped[JsonValue] = mapped_column(JSON, nullable=True)


class EvidenceRow(EntityRow):
    __tablename__ = "evidence"
    website_audit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("website_audits.id"), nullable=True, index=True
    )
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_evidence_confidence"),
    )
    company_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    source_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sources.id"), index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(1000))
    statement: Mapped[str] = mapped_column(String(1000))
    raw_value: Mapped[JsonValue] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class CampaignRow(EntityRow):
    __tablename__ = "campaigns"
    name: Mapped[str] = mapped_column(String(1000))
    campaign_type: Mapped[CampaignType] = mapped_column(
        Enum(
            CampaignType, native_enum=False, create_constraint=True, name="campaigns_campaign_type"
        )
    )
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    geography: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LeadRow(EntityRow):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("company_id", "campaign_id", name="uq_lead_company_campaign"),
        CheckConstraint("priority >= 0 AND priority <= 100", name="ck_lead_priority"),
    )
    company_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("companies.id"), index=True
    )
    campaign_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("campaigns.id"), index=True
    )
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, native_enum=False, create_constraint=True, name="leads_status")
    )
    priority: Mapped[int] = mapped_column(Integer)
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LeadScoreRow(EntityRow):
    __tablename__ = "lead_scores"
    __table_args__ = (
        CheckConstraint("total_score >= 0 AND total_score <= 100", name="ck_score_total"),
    )
    lead_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id"), index=True)
    total_score: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    scoring_version: Mapped[str] = mapped_column(String(1000))
    calculated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    explanation: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class ScoreComponentRow(EntityRow):
    __tablename__ = "score_components"
    __table_args__ = (
        UniqueConstraint("lead_score_id", "criterion", name="uq_score_criterion"),
        CheckConstraint(
            "points_awarded >= 0 AND max_points > 0 AND max_points <= 100 "
            "AND points_awarded <= max_points",
            name="ck_component_points",
        ),
    )
    lead_score_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("lead_scores.id"), index=True
    )
    criterion: Mapped[str] = mapped_column(String(1000))
    points_awarded: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    max_points: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    explanation: Mapped[str] = mapped_column(String(1000))


class LeadInteractionRow(EntityRow):
    __tablename__ = "lead_interactions"
    lead_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("leads.id"), index=True)
    contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("contacts.id"), nullable=True, index=True
    )
    interaction_type: Mapped[InteractionType] = mapped_column(
        Enum(
            InteractionType,
            native_enum=False,
            create_constraint=True,
            name="lead_interactions_interaction_type",
        )
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    outcome: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class CompanyIdentityRow(Base):
    __tablename__ = "company_identities"
    key: Mapped[str] = mapped_column(String(2048), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)


class ComponentEvidenceRow(Base):
    __tablename__ = "component_evidence"
    component_id: Mapped[UUID] = mapped_column(ForeignKey("score_components.id"), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("evidence.id"), primary_key=True)


class DiscoveryRunRow(EntityRow):
    __tablename__ = "discovery_runs"
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    provider: Mapped[str] = mapped_column(String(1000))
    query: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[DiscoveryStatus] = mapped_column(
        Enum(
            DiscoveryStatus,
            native_enum=False,
            create_constraint=True,
            name="discovery_run_status",
        )
    )
    outcomes: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSON)
    provider_error: Mapped[str | None] = mapped_column(String(4000), nullable=True)


class WebsiteAuditRow(EntityRow):
    __tablename__ = "website_audits"
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    website_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    final_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[AuditStatus] = mapped_column(
        Enum(AuditStatus, native_enum=False, create_constraint=True, name="website_audit_status")
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_size_bytes: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
