"""Untrusted candidate DTOs and provider-neutral discovery contracts."""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from lead_engine.application.ports import UnitOfWork
from lead_engine.domain.campaign_profiles import (
    CampaignConfiguration,
    CampaignGeography,
    CampaignProfile,
    get_campaign_profile,
)
from lead_engine.domain.enums import SourceType
from lead_engine.domain.models import Campaign, Company, utc_now


class DiscoveryQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
    campaign_id: UUID
    country: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    industry: str | None = None
    city: str | None = None
    region: str | None = None
    subindustry: str | None = None
    query_text: str | None = None
    limit: Annotated[int, Field(ge=1, le=10000)] = 1000


def discovery_query_for_profile(
    campaign_id: UUID,
    profile: CampaignProfile,
    configuration: CampaignConfiguration,
    *,
    query_text: str | None = None,
    limit: int = 1000,
) -> DiscoveryQuery:
    """Derive provider-neutral query defaults from vertical and campaign data."""

    if configuration.profile_id != profile.profile_id:
        raise ValueError("Campaign configuration profile_id does not match profile")
    geography = configuration.geography
    return DiscoveryQuery(
        campaign_id=campaign_id,
        country=geography.country or profile.default_country,
        region=geography.region or profile.default_region,
        city=geography.city or profile.default_city,
        industry=configuration.industry or profile.default_industry,
        subindustry=configuration.subindustry,
        query_text=query_text,
        limit=limit,
    )


def campaign_configuration(campaign: Campaign) -> CampaignConfiguration:
    """Copy structured campaign targeting without parsing legacy geography text."""

    return CampaignConfiguration(
        profile_id=campaign.profile_id,
        geography=CampaignGeography(
            country=campaign.country,
            region=campaign.region,
            city=campaign.city,
        ),
        industry=campaign.industry,
        subindustry=campaign.subindustry,
    )


def discovery_query_for_campaign(
    campaign: Campaign,
    *,
    query_text: str | None = None,
    limit: int = 1000,
) -> DiscoveryQuery:
    """Create a query from a persisted campaign's profile and structured targeting."""

    return discovery_query_for_profile(
        campaign.id,
        get_campaign_profile(campaign.profile_id),
        campaign_configuration(campaign),
        query_text=query_text,
        limit=limit,
    )


class DiscoveryCandidate(BaseModel):
    """Raw fields intentionally tolerate missing/invalid facts until Scout validates them."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    external_id: str | None = None
    name: str | None = None
    legal_name: str | None = None
    website: str | None = None
    primary_domain: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    industry: str | None = None
    subindustry: str | None = None
    instagram_url: str | None = None
    facebook_url: str | None = None
    linkedin_url: str | None = None
    google_maps_url: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    retrieved_at: AwareDatetime = Field(default_factory=utc_now)
    validation_error: str | None = None

    def to_company(self) -> Company:
        if self.validation_error:
            raise ValueError(self.validation_error)
        fields = {
            field: value.strip() or None if isinstance(value, str) else value
            for field, value in self.model_dump().items()
            if field in Company.model_fields
        }
        fields["canonical_name"] = self.name.strip() if self.name else ""
        return Company.model_validate(fields)


class DiscoveryProvider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def source_type(self) -> SourceType: ...
    def discover(self, query: DiscoveryQuery) -> Iterable[DiscoveryCandidate]: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[UnitOfWork]: ...
