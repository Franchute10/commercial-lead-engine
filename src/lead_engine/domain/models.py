"""Validated immutable records. All observations reference a separately stored source."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from lead_engine.domain.enums import (
    CampaignType,
    InteractionType,
    LeadStatus,
    RoleCategory,
    SourceType,
)
from lead_engine.domain.identity import normalize_domain, normalize_name, normalize_url

Text = Annotated[str, Field(min_length=1, max_length=1000)]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Points = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
    id: UUID = Field(default_factory=uuid4)


class Timestamped(Entity):
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def ordered_times(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class Company(Timestamped):
    canonical_name: Text
    legal_name: Text | None = None
    website: Text | None = None
    primary_domain: Text | None = None
    industry: Text | None = None
    subindustry: Text | None = None
    city: Text | None = None
    region: Text | None = None
    country: Text | None = None
    address: Text | None = None
    phone: Text | None = None
    email: Text | None = None
    instagram_url: Text | None = None
    facebook_url: Text | None = None
    linkedin_url: Text | None = None
    google_maps_url: Text | None = None

    @field_validator("website", "instagram_url", "facebook_url", "linkedin_url", "google_maps_url")
    @classmethod
    def urls(cls, value: str | None) -> str | None:
        return normalize_url(value) if value is not None else None

    @field_validator("primary_domain")
    @classmethod
    def domain(cls, value: str | None) -> str | None:
        return normalize_domain(value) if value is not None else None

    @model_validator(mode="after")
    def valid_identity(self) -> Self:
        if not normalize_name(self.canonical_name):
            raise ValueError("Company name must contain letters or digits")
        if self.legal_name and not normalize_name(self.legal_name):
            raise ValueError("Legal name must contain letters or digits")
        for value in (self.city, self.country):
            if value is not None and not normalize_name(value):
                raise ValueError("Location must contain letters or digits")
        if self.website and self.primary_domain:
            if normalize_domain(self.website) != self.primary_domain:
                raise ValueError("Website and primary_domain disagree")
        return self


class Contact(Timestamped):
    company_id: UUID
    full_name: Text
    role_title: Text | None = None
    role_category: RoleCategory = RoleCategory.UNKNOWN
    linkedin_url: Text | None = None
    email: Text | None = None
    phone: Text | None = None
    confidence: Confidence | None = None

    @field_validator("linkedin_url")
    @classmethod
    def url(cls, value: str | None) -> str | None:
        return normalize_url(value) if value else None


class Source(Entity):
    source_type: SourceType
    url: Text | None = None
    title: Text | None = None
    retrieved_at: AwareDatetime = Field(default_factory=utc_now)
    metadata: dict[str, JsonValue] | None = None

    @field_validator("url")
    @classmethod
    def normalize(cls, value: str | None) -> str | None:
        return normalize_url(value) if value else None


class Evidence(Entity):
    website_audit_id: UUID | None = None
    company_id: UUID
    source_id: UUID
    evidence_type: Text
    statement: Text
    raw_value: JsonValue = None
    confidence: Confidence
    observed_at: AwareDatetime
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Campaign(Timestamped):
    name: Text
    campaign_type: CampaignType
    description: Text | None = None
    geography: Text | None = None
    active: bool = True


class Lead(Timestamped):
    company_id: UUID
    campaign_id: UUID
    status: LeadStatus = LeadStatus.DISCOVERED
    priority: Annotated[int, Field(ge=0, le=100)] = 0
    assigned_at: AwareDatetime | None = None


class ScoreComponent(Entity):
    lead_score_id: UUID
    criterion: Text
    points_awarded: Points
    max_points: Points
    explanation: Text
    evidence_ids: tuple[UUID, ...] = ()

    @field_validator("evidence_ids")
    @classmethod
    def stable_evidence_order(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return tuple(sorted(value))

    @model_validator(mode="after")
    def points_within_max(self) -> Self:
        if self.max_points <= 0 or self.points_awarded > self.max_points:
            raise ValueError("Points must not exceed a positive maximum")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate evidence references")
        return self


class LeadScore(Entity):
    lead_id: UUID
    total_score: Points
    scoring_version: Text
    calculated_at: AwareDatetime = Field(default_factory=utc_now)
    explanation: Text | None = None
    components: tuple[ScoreComponent, ...]

    @field_validator("components")
    @classmethod
    def stable_component_order(
        cls, value: tuple[ScoreComponent, ...]
    ) -> tuple[ScoreComponent, ...]:
        return tuple(sorted(value, key=lambda component: component.criterion))

    @model_validator(mode="after")
    def reproducible(self) -> Self:
        if not self.components:
            raise ValueError("Score requires components")
        if any(component.lead_score_id != self.id for component in self.components):
            raise ValueError("Components must reference their parent score")
        if len({c.criterion for c in self.components}) != len(self.components):
            raise ValueError("Duplicate score criteria")
        if sum(c.max_points for c in self.components) != Decimal(100):
            raise ValueError("Component maximums must sum to 100")
        if sum(c.points_awarded for c in self.components) != self.total_score:
            raise ValueError("Total must equal component points")
        return self


class LeadInteraction(Entity):
    lead_id: UUID
    contact_id: UUID | None = None
    interaction_type: InteractionType
    occurred_at: AwareDatetime
    outcome: Text | None = None
    notes: Text | None = None
