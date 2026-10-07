"""Human pilot labels are validation records, never commercial evidence or score inputs."""

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from lead_engine.domain.models import Entity, Text
from lead_engine.domain.shortlist import ShortlistItem


class ContactDecision(StrEnum):
    YES = "YES"
    NO = "NO"
    MAYBE = "MAYBE"


class DecisionMakerQuality(StrEnum):
    GOOD = "GOOD"
    PARTIAL = "PARTIAL"
    WRONG = "WRONG"
    UNKNOWN = "UNKNOWN"


class OutputQuality(StrEnum):
    GOOD = "GOOD"
    PARTIAL = "PARTIAL"
    WRONG = "WRONG"


class PilotEvaluation(Entity):
    lead_id: UUID
    company_id: UUID
    campaign_id: UUID
    shortlist_run_id: UUID
    evaluator: Text = "Frank"
    evaluated_at: AwareDatetime
    revision: int = Field(ge=1)
    evaluation_version: Text = "pilot-evaluation-v1"
    would_contact: ContactDecision
    decision_maker_quality: DecisionMakerQuality
    opportunity_quality: OutputQuality
    outreach_quality: OutputQuality
    evaluator_notes: Text | None = None
    score_id: UUID | None = None
    commercial_brief_id: UUID | None = None
    recommended_contact_id: UUID | None = None
    outreach_draft_id: UUID | None = None


class PilotReviewRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item: ShortlistItem
    evaluation: PilotEvaluation | None
    needs_more_research: bool


class PilotReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    campaign_id: UUID
    campaign_name: Text
    evaluator: Text
    generated_at: AwareDatetime
    shortlist_run_id: UUID
    shortlist_generated_at: AwareDatetime
    shortlisted_companies: int
    companies_evaluated: int
    unevaluated_lead_ids: tuple[UUID, ...]
    would_contact_distribution: dict[ContactDecision, int]
    precision_denominator: int
    shortlist_precision_percent: float | None
    would_contact_percent: float | None
    decision_maker_quality_distribution: dict[DecisionMakerQuality, int]
    opportunity_quality_distribution: dict[OutputQuality, int]
    outreach_quality_distribution: dict[OutputQuality, int]
    false_positive_lead_ids: tuple[UUID, ...]
    leads_needing_more_research: tuple[UUID, ...]
    rows: tuple[PilotReviewRow, ...]
