"""Versioned shortlist contracts, snapshots and temporary human suppressions."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lead_engine.domain.audit import WebsiteAudit
from lead_engine.domain.contact_research import ContactRecommendation
from lead_engine.domain.enums import CampaignType, InteractionType, LeadStatus, RoleCategory
from lead_engine.domain.models import (
    Campaign,
    Company,
    Entity,
    Evidence,
    Lead,
    LeadInteraction,
    LeadScore,
    Text,
)
from lead_engine.domain.research import (
    BriefStatus,
    CommercialBrief,
    OpportunityPriority,
    OpportunityType,
)
from lead_engine.domain.scoring import ScoreBand


class NextAction(StrEnum):
    RESEARCH_MORE = "RESEARCH_MORE"
    FIND_DECISION_MAKER = "FIND_DECISION_MAKER"
    SEND_LINKEDIN_CONNECTION = "SEND_LINKEDIN_CONNECTION"
    SEND_LINKEDIN_MESSAGE = "SEND_LINKEDIN_MESSAGE"
    SEND_EMAIL = "SEND_EMAIL"
    SEND_WHATSAPP = "SEND_WHATSAPP"
    MAKE_PHONE_CALL = "MAKE_PHONE_CALL"
    FOLLOW_UP = "FOLLOW_UP"
    PREPARE_MEETING = "PREPARE_MEETING"
    PREPARE_PROPOSAL = "PREPARE_PROPOSAL"
    NO_ACTION = "NO_ACTION"
    REVIEW_MANUALLY = "REVIEW_MANUALLY"


class SuppressionReason(StrEnum):
    LOW_SCORE = "LOW_SCORE"
    INSUFFICIENT_RESEARCH = "INSUFFICIENT_RESEARCH"
    RECENT_CONTACT = "RECENT_CONTACT"
    PIPELINE_NOT_ACTIONABLE = "PIPELINE_NOT_ACTIONABLE"
    WON = "WON"
    LOST = "LOST"
    ARCHIVED = "ARCHIVED"
    NO_CLEAR_OPPORTUNITY = "NO_CLEAR_OPPORTUNITY"
    STALE_DATA = "STALE_DATA"
    MANUAL_SUPPRESSION = "MANUAL_SUPPRESSION"
    FILTER_MISMATCH = "FILTER_MISMATCH"
    DUPLICATE_COMPANY = "DUPLICATE_COMPANY"


class ShortlistValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ShortlistSettings(ShortlistValue):
    score_freshness_days: int = Field(default=30, ge=1, le=365)
    brief_freshness_days: int = Field(default=30, ge=1, le=365)
    contact_freshness_days: int = Field(default=30, ge=1, le=365)
    audit_freshness_days: int = Field(default=30, ge=1, le=365)
    maximum_score_age_days: int = Field(default=90, ge=1, le=730)
    minimum_lead_score: int = Field(default=40, ge=0, le=100)
    cooldowns: dict[InteractionType, int] = Field(
        default_factory=lambda: {
            InteractionType.LINKEDIN_CONNECTION: 7,
            InteractionType.LINKEDIN_MESSAGE: 7,
            InteractionType.EMAIL: 7,
            InteractionType.WHATSAPP: 7,
            InteractionType.PHONE_CALL: 3,
            InteractionType.FOLLOW_UP: 7,
            InteractionType.PROPOSAL_SENT: 10,
        }
    )
    channel_preference: tuple[str, ...] = (
        "LINKEDIN",
        "EMAIL",
        "PHONE",
        "WEBSITE_CONTACT_FORM",
        "WHATSAPP",
    )

    @model_validator(mode="after")
    def safe_settings(self) -> Self:
        required = {
            InteractionType.LINKEDIN_CONNECTION,
            InteractionType.LINKEDIN_MESSAGE,
            InteractionType.EMAIL,
            InteractionType.WHATSAPP,
            InteractionType.PHONE_CALL,
            InteractionType.FOLLOW_UP,
            InteractionType.PROPOSAL_SENT,
        }
        if set(self.cooldowns) != required or any(
            not 1 <= days <= 365 for days in self.cooldowns.values()
        ):
            raise ValueError(
                "All outbound cooldowns must be explicitly configured between 1 and 365 days"
            )
        if self.maximum_score_age_days < self.score_freshness_days:
            raise ValueError("Maximum score age must cover score freshness")
        allowed = {"LINKEDIN", "EMAIL", "PHONE", "WEBSITE_CONTACT_FORM", "WHATSAPP"}
        if (
            not self.channel_preference
            or not set(self.channel_preference) <= allowed
            or len(set(self.channel_preference)) != len(self.channel_preference)
        ):
            raise ValueError(
                "Channel preference must contain distinct supported public channel types"
            )
        return self


class ShortlistFilters(ShortlistValue):
    campaign_id: UUID | None = None
    campaign_type: CampaignType | None = None
    city: str | None = None
    minimum_lead_score: int | None = Field(default=None, ge=0, le=100)
    minimum_research_completeness: int = Field(default=0, ge=0, le=100)
    pipeline_status: LeadStatus | None = None
    opportunity_type: OpportunityType | None = None
    limit: int = Field(default=5, ge=1, le=100)


class PriorityComponent(ShortlistValue):
    dimension: str
    points: int = Field(ge=0, le=100)
    maximum_points: int = Field(ge=1, le=100)
    explanation: str

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.points > self.maximum_points:
            raise ValueError("Priority component exceeds maximum")
        return self


class ShortlistItem(ShortlistValue):
    lead_id: UUID
    company_id: UUID
    company_name: Text
    campaign_name: Text
    campaign_type: CampaignType
    latest_score: float | None
    score_id: UUID | None
    score_band: ScoreBand | None
    score_completeness: int | None
    brief_id: UUID | None
    research_status: BriefStatus | None
    research_completeness: int
    primary_opportunity: OpportunityType
    opportunity_priority: OpportunityPriority
    recommended_contact_id: UUID | None
    recommended_contact_name: str | None
    recommended_contact_role: str | None
    recommended_contact_fit: int | None
    recommended_channel: str | None
    recommended_channel_value: str | None
    channel_evidence_id: UUID | None
    target_roles: tuple[RoleCategory, ...]
    suggested_contact_angle: str
    pipeline_status: LeadStatus
    last_interaction_at: AwareDatetime | None
    last_interaction_id: UUID | None
    eligible_again_at: AwareDatetime | None
    recommended_next_action: NextAction
    shortlist_priority_score: int = Field(ge=0, le=100)
    shortlist_rank: int | None = Field(default=None, ge=1)
    components: tuple[PriorityComponent, ...]
    warnings: tuple[str, ...]
    reasons: tuple[str, ...]
    evidence_ids: tuple[UUID, ...]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            sum(c.maximum_points for c in self.components) != 100
            or sum(c.points for c in self.components) != self.shortlist_priority_score
        ):
            raise ValueError("Shortlist priority must equal its 100-point components")
        if len({c.dimension for c in self.components}) != len(self.components):
            raise ValueError("Duplicate priority dimensions")
        if (self.recommended_channel is None) != (self.channel_evidence_id is None):
            raise ValueError("A recommended channel requires public evidence")
        if self.channel_evidence_id and self.channel_evidence_id not in self.evidence_ids:
            raise ValueError("Channel evidence must be traceable")
        return self


class ShortlistDecision(ShortlistValue):
    item: ShortlistItem
    suppression_reasons: tuple[SuppressionReason, ...] = ()


class DailyShortlistRun(Entity):
    generated_at: AwareDatetime
    policy_version: Text
    filters: ShortlistFilters
    settings: ShortlistSettings
    candidates_considered: int = Field(ge=0)
    candidates_suppressed: int = Field(ge=0)
    items_returned: int = Field(ge=0)
    items: tuple[ShortlistItem, ...]
    suppressed: tuple[ShortlistDecision, ...]
    eligible_not_selected: tuple[UUID, ...]

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.items_returned != len(self.items) or self.candidates_suppressed != len(
            self.suppressed
        ):
            raise ValueError("Run counts mismatch")
        ids = (
            [i.lead_id for i in self.items]
            + [d.item.lead_id for d in self.suppressed]
            + list(self.eligible_not_selected)
        )
        if len(ids) != self.candidates_considered or len(set(ids)) != len(ids):
            raise ValueError("Run candidate identities/counts mismatch")
        if [i.shortlist_rank for i in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("Ranks must match persisted ordering")
        if any(not d.suppression_reasons for d in self.suppressed):
            raise ValueError("Suppressed lead requires reasons")
        if self.items_returned > self.filters.limit:
            raise ValueError("Run exceeds requested limit")
        return self


class ShortlistSuppression(Entity):
    lead_id: UUID
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    reason: Text
    revoked_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_times(self) -> Self:
        if self.expires_at <= self.starts_at or (
            self.revoked_at and self.revoked_at < self.starts_at
        ):
            raise ValueError("Invalid suppression interval")
        return self

    def active_at(self, as_of: datetime) -> bool:
        return self.starts_at <= as_of < self.expires_at and (
            self.revoked_at is None or self.revoked_at > as_of
        )


@dataclass(frozen=True)
class ShortlistContext:
    lead: Lead
    company: Company
    campaign: Campaign
    score: LeadScore | None
    brief: CommercialBrief | None
    contacts: tuple[ContactRecommendation, ...]
    target_roles: tuple[RoleCategory, ...]
    interactions: tuple[LeadInteraction, ...]
    audits: tuple[WebsiteAudit, ...]
    evidence: tuple[Evidence, ...]
    suppressions: tuple[ShortlistSuppression, ...]
    as_of: datetime


class ShortlistPolicy(Protocol):
    @property
    def version(self) -> str: ...
    def evaluate(
        self, context: ShortlistContext, filters: ShortlistFilters, settings: ShortlistSettings
    ) -> ShortlistDecision: ...
