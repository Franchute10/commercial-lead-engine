"""Attributable contact observations and separate, deterministic contact fit."""

from datetime import timedelta
from enum import StrEnum
from typing import Literal, Protocol, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from lead_engine.domain.enums import CampaignType, RoleCategory, SourceType
from lead_engine.domain.identity import normalize_name, normalize_url
from lead_engine.domain.models import Company, Confidence, Entity, Text, utc_now


class VerificationState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    SUPPORTED = "SUPPORTED"
    VERIFIED = "VERIFIED"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"


class ContactCandidate(Entity):
    full_name: Text
    role_title: Text | None = None
    role_category: RoleCategory = RoleCategory.UNKNOWN
    company_name: Text | None = None
    company_id: UUID | None = None
    linkedin_url: Text | None = None
    public_profile_url: Text | None = None
    public_email: Text | None = None
    public_phone: Text | None = None
    source_url: Text
    source_title: Text | None = None
    source_type: SourceType = SourceType.MANUAL
    observed_at: AwareDatetime = Field(default_factory=utc_now)
    confidence: Confidence = 0.7
    association_statement: Text | None = None
    context: str | None = None
    context_statement: Text | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("source_url", "linkedin_url", "public_profile_url")
    @classmethod
    def urls(cls, value: str | None) -> str | None:
        return normalize_url(value) if value is not None else None

    @field_validator("public_email")
    @classmethod
    def email(cls, value: str | None) -> str | None:
        if value is not None and ("@" not in value or any(c.isspace() for c in value)):
            raise ValueError("Supply an explicitly published email")
        return value.casefold() if value else None

    @model_validator(mode="after")
    def context_evidence(self) -> Self:
        if self.context and not self.context_statement:
            raise ValueError("Company context requires an attributable statement")
        return self

    @field_validator("context")
    @classmethod
    def known_context(cls, value: str | None) -> str | None:
        if value not in {None, "independent", "group", "small_clinic"}:
            raise ValueError("Unsupported evidenced company context")
        return value


class ContactIdentity(Entity):
    key: Text
    contact_id: UUID


class DecisionMakerResearchRun(Entity):
    lead_id: UUID | None = None
    company_id: UUID
    provider: Text
    started_at: AwareDatetime
    finished_at: AwareDatetime
    candidates_found: int = Field(ge=0)
    contacts_created: int = Field(ge=0)
    contacts_reused: int = Field(ge=0)
    conflicts: int = Field(ge=0)
    status: Literal["SUCCESS", "PARTIAL", "FAILED"]
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def run_times(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("Research cannot finish before it starts")
        return self


class ResearchValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PublicChannel(ResearchValue):
    channel_type: str
    value: Text
    evidence_id: UUID


class ContactRecommendation(ResearchValue):
    contact_id: UUID
    full_name: Text
    current_role: Text
    role_category: RoleCategory
    fit_score: int = Field(ge=0, le=100)
    confidence: str
    verification: VerificationState
    channels: list[PublicChannel]
    evidence_ids: list[UUID]
    explanation: list[str]
    warnings: list[str]


class RecommendationResult(ResearchValue):
    lead_id: UUID
    contacts: list[ContactRecommendation]
    target_roles: list[RoleCategory]
    message: str


class ContactDiscoveryProvider(Protocol):
    name: str

    def discover(self, company: Company) -> list[ContactCandidate]: ...
    @property
    def warnings(self) -> list[str]: ...


class PublicSearchProvider(Protocol):
    def search(self, query: str, limit: int = 5) -> list[ContactCandidate]: ...


def identity_keys(candidate: ContactCandidate, company_id: UUID) -> list[str]:
    keys = []
    for url in (candidate.linkedin_url, candidate.public_profile_url):
        if url:
            parsed = urlsplit(url)
            keys.append(f"{company_id}:profile:{parsed.hostname}{parsed.path.rstrip('/')}")
    if candidate.public_email:
        keys.append(f"{company_id}:email:{candidate.public_email}")
    keys.append(
        f"{company_id}:name:{normalize_name(candidate.full_name)}:{candidate.role_category}"
    )
    return keys


def source_reliability(candidate: ContactCandidate, company: Company) -> int:
    official = urlsplit(company.website or "").hostname
    host = urlsplit(candidate.source_url).hostname
    if official and host == official:
        return 10
    return {
        SourceType.GOVERNMENT_REGISTRY: 9,
        SourceType.LINKEDIN_PUBLIC: 8,
        SourceType.INSTAGRAM_PUBLIC: 7,
        SourceType.FACEBOOK_PUBLIC: 7,
        SourceType.BUSINESS_DIRECTORY: 6,
        SourceType.GOOGLE_SEARCH: 2,
        SourceType.MANUAL: 6,
    }.get(candidate.source_type, 5)


def is_supported(candidate: ContactCandidate, company: Company) -> bool:
    association = candidate.company_id == company.id or (
        candidate.company_name is not None
        and normalize_name(candidate.company_name) == normalize_name(company.canonical_name)
    )
    return bool(
        association
        and candidate.association_statement
        and candidate.role_title
        and candidate.confidence >= 0.7
        and candidate.source_type != SourceType.GOOGLE_SEARCH
        and candidate.observed_at <= utc_now()
    )


def role_points(
    category: RoleCategory, campaign: CampaignType, context: str | None, title: str
) -> int:
    weights = {
        CampaignType.HEALTH: {
            "OWNER": 50,
            "FOUNDER": 50,
            "GENERAL_MANAGEMENT": 46,
            "MARKETING": 44,
            "DIGITAL": 44,
            "MEDICAL_DIRECTOR": 42,
            "COMMERCIAL": 36,
            "ADMINISTRATION": 25,
        },
        CampaignType.CONSTRUCTION: {
            "OWNER": 50,
            "FOUNDER": 50,
            "GENERAL_MANAGEMENT": 46,
            "COMMERCIAL": 44,
            "MARKETING": 40,
            "DIGITAL": 38,
            "ADMINISTRATION": 25,
        },
        CampaignType.HOSPITALITY: {
            "OWNER": 45,
            "FOUNDER": 45,
            "MARKETING": 46,
            "GENERAL_MANAGEMENT": 42,
            "CUSTOMER_EXPERIENCE": 40,
            "DIGITAL": 40,
            "COMMERCIAL": 35,
            "ADMINISTRATION": 25,
        },
    }
    points = weights[campaign].get(category.value, 10)
    if campaign == CampaignType.HOSPITALITY:
        if context == "independent" and category in {RoleCategory.OWNER, RoleCategory.FOUNDER}:
            points = 50
        if context == "group":
            if category in {RoleCategory.MARKETING, RoleCategory.DIGITAL}:
                points = 50
            if "restaurant" in normalize_name(title) or "restaurante" in normalize_name(title):
                points = min(points, 25)
    if (
        campaign == CampaignType.HEALTH
        and context == "small_clinic"
        and category == RoleCategory.MEDICAL_DIRECTOR
    ):
        points = 50
    return points


FRESHNESS = timedelta(days=30)
