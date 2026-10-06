"""Immutable commercial briefs and provider-free research policy contracts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lead_engine.domain.audit import AuditStatus
from lead_engine.domain.contact_research import ContactRecommendation
from lead_engine.domain.enums import CampaignType, LeadStatus, RoleCategory
from lead_engine.domain.models import Entity, LeadScore, Source, Text, utc_now
from lead_engine.domain.scoring import ScoreBand, ScoringContext


class OpportunityType(StrEnum):
    APPOINTMENT_CONVERSION = "APPOINTMENT_CONVERSION"
    RESERVATION_CONVERSION = "RESERVATION_CONVERSION"
    QUOTE_CONVERSION = "QUOTE_CONVERSION"
    PRODUCT_DISCOVERY = "PRODUCT_DISCOVERY"
    SERVICE_DISCOVERY = "SERVICE_DISCOVERY"
    CATALOG_IMPROVEMENT = "CATALOG_IMPROVEMENT"
    B2B_LEAD_CAPTURE = "B2B_LEAD_CAPTURE"
    PRIVATE_EVENTS = "PRIVATE_EVENTS"
    CUSTOMER_JOURNEY = "CUSTOMER_JOURNEY"
    DIGITAL_TRUST = "DIGITAL_TRUST"
    CONTACTABILITY = "CONTACTABILITY"
    ECOMMERCE = "ECOMMERCE"
    BRAND_EXPERIENCE = "BRAND_EXPERIENCE"
    LOCAL_DISCOVERY = "LOCAL_DISCOVERY"
    MULTI_LOCATION_EXPERIENCE = "MULTI_LOCATION_EXPERIENCE"
    NO_CLEAR_OPPORTUNITY = "NO_CLEAR_OPPORTUNITY"


class OpportunityPriority(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class BriefStatus(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class BriefValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Opportunity(BriefValue):
    opportunity_type: OpportunityType
    title: Text
    reasons: tuple[Text, ...] = Field(min_length=2)
    supporting_evidence_ids: tuple[UUID, ...] = Field(min_length=2)
    strength: int = Field(ge=1, le=3)

    @model_validator(mode="after")
    def real_opportunity(self) -> Self:
        if self.opportunity_type == OpportunityType.NO_CLEAR_OPPORTUNITY:
            raise ValueError("No clear opportunity is represented by no primary opportunity")
        if len(set(self.supporting_evidence_ids)) != len(self.supporting_evidence_ids):
            raise ValueError("Opportunity evidence must be distinct")
        return self


class CompletenessCategory(BriefValue):
    category: str
    maximum_points: int = Field(ge=1, le=100)
    awarded_points: int = Field(ge=0, le=100)
    explanation: str
    evidence_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def bounded_points(self) -> Self:
        if self.awarded_points > self.maximum_points:
            raise ValueError("Completeness points exceed maximum")
        return self


class EvidenceCitation(BriefValue):
    evidence_id: UUID
    source_id: UUID
    source_url: str | None
    source_title: str | None
    evidence_type: str
    statement: str
    observed_at: AwareDatetime


class CommercialBrief(Entity):
    lead_id: UUID
    company_id: UUID
    campaign_id: UUID
    generated_at: AwareDatetime = Field(default_factory=utc_now)
    research_version: Text
    company_name: Text
    campaign_name: Text
    company_summary: Text
    opportunity_type: OpportunityType
    opportunity_priority: OpportunityPriority
    primary_opportunity: Opportunity | None
    secondary_opportunities: tuple[Opportunity, ...] = Field(max_length=3)
    supporting_evidence_ids: tuple[UUID, ...]
    evidence_citations: tuple[EvidenceCitation, ...]
    decision_maker_recommendations: tuple[ContactRecommendation, ...] = Field(max_length=3)
    target_roles: tuple[RoleCategory, ...]
    suggested_contact_angle: Text
    missing_information: tuple[Text, ...]
    warnings: tuple[Text, ...]
    data_completeness: int = Field(ge=0, le=100)
    completeness_categories: tuple[CompletenessCategory, ...]
    latest_lead_score: float | None = Field(ge=0, le=100, allow_inf_nan=False)
    lead_score_id: UUID | None
    score_band: ScoreBand | None
    score_calculated_at: AwareDatetime | None
    website_audit_id: UUID | None
    website_audit_status: AuditStatus | None
    score_current: bool
    decision_maker_available: bool
    pipeline_status: LeadStatus
    status: BriefStatus

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if sum(c.maximum_points for c in self.completeness_categories) != 100:
            raise ValueError("Research completeness maximums must sum to 100")
        if sum(c.awarded_points for c in self.completeness_categories) != self.data_completeness:
            raise ValueError("Research completeness must equal awarded points")
        if len({c.category for c in self.completeness_categories}) != len(
            self.completeness_categories
        ):
            raise ValueError("Duplicate completeness categories")
        if self.primary_opportunity is None:
            if (
                self.opportunity_type != OpportunityType.NO_CLEAR_OPPORTUNITY
                or self.secondary_opportunities
            ):
                raise ValueError("No primary opportunity requires an empty opportunity set")
            if (
                self.status != BriefStatus.INSUFFICIENT_DATA
                or self.opportunity_priority != OpportunityPriority.NONE
            ):
                raise ValueError("No opportunity requires insufficient data and no priority")
        elif self.opportunity_type != self.primary_opportunity.opportunity_type:
            raise ValueError("Primary opportunity type mismatch")
        opportunities = (
            (self.primary_opportunity,) if self.primary_opportunity else ()
        ) + self.secondary_opportunities
        if len({o.opportunity_type for o in opportunities}) != len(opportunities):
            raise ValueError("Duplicate opportunity types")
        refs = {eid for o in opportunities for eid in o.supporting_evidence_ids}
        if refs != set(self.supporting_evidence_ids):
            raise ValueError("Opportunity evidence union mismatch")
        citations = {c.evidence_id for c in self.evidence_citations}
        required = refs | {
            eid for contact in self.decision_maker_recommendations for eid in contact.evidence_ids
        }
        required |= {
            eid for category in self.completeness_categories for eid in category.evidence_ids
        }
        if not required <= citations:
            raise ValueError("All referenced evidence must have source citations")
        if self.decision_maker_recommendations and self.target_roles:
            raise ValueError("Use contacts or role fallback, not both")
        if any(
            channel.evidence_id not in contact.evidence_ids
            for contact in self.decision_maker_recommendations
            for channel in contact.channels
        ):
            raise ValueError("Public channels must cite the contact evidence")
        if self.latest_lead_score is None and (
            self.score_band is not None
            or self.score_calculated_at is not None
            or self.score_current
        ):
            raise ValueError("Missing score cannot have current status, band or calculation date")
        if self.latest_lead_score is not None and (
            self.score_band is None or self.score_calculated_at is None
        ):
            raise ValueError("Stored score requires band and calculation date")
        if (self.website_audit_id is None) != (self.website_audit_status is None):
            raise ValueError("Audit ID and status must be paired")
        if (self.latest_lead_score is None) != (self.lead_score_id is None):
            raise ValueError("Score value and ID must be paired")
        return self


@dataclass(frozen=True)
class ResearchContext:
    observations: ScoringContext
    sources: tuple[Source, ...]
    latest_score: LeadScore | None
    score_band: ScoreBand | None
    recommendations: tuple[ContactRecommendation, ...]
    target_roles: tuple[RoleCategory, ...]


class ResearchPolicy(Protocol):
    @property
    def version(self) -> str: ...
    @property
    def campaign_type(self) -> CampaignType: ...
    def generate(self, context: ResearchContext) -> CommercialBrief: ...
