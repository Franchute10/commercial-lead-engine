"""Immutable vertical profiles separated from campaign geography and persistence."""

import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, field_validator

from lead_engine.domain.contact_research import DEFAULT_TARGET_ROLES
from lead_engine.domain.enums import CampaignType, RoleCategory

_PROFILE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
_POLICY_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class CampaignProfile(BaseModel):
    """Versioned vertical behavior references without implementation objects."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    profile_id: str
    version: str
    name: str
    campaign_type: CampaignType
    scoring_profile: str
    research_profile: str
    shortlist_profile: str
    default_country: str | None = None
    default_region: str | None = None
    default_city: str | None = None
    default_industry: str | None = None
    target_roles: tuple[RoleCategory, ...] = ()

    @field_validator("profile_id")
    @classmethod
    def valid_profile_id(cls, value: str) -> str:
        if not _PROFILE_ID.fullmatch(value):
            raise ValueError("profile_id must be a lowercase kebab-case identifier")
        return value

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        if not _VERSION.fullmatch(value):
            raise ValueError("version must be a numeric dotted version")
        return value

    @field_validator("scoring_profile", "research_profile", "shortlist_profile")
    @classmethod
    def valid_policy_reference(cls, value: str) -> str:
        if not _POLICY_ID.fullmatch(value):
            raise ValueError("policy references must be lowercase kebab-case identifiers")
        return value

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value:
            raise ValueError("name must not be blank")
        return value


class CampaignGeography(BaseModel):
    """Text location overrides; no geocoding or hierarchy inference is performed."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    country: str | None = None
    region: str | None = None
    city: str | None = None

    @field_validator("country", "region", "city")
    @classmethod
    def nonblank_location(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("location values must not be blank")
        return value


class CampaignConfiguration(BaseModel):
    """Campaign-level data kept out of the immutable vertical profile."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    profile_id: str
    geography: CampaignGeography = CampaignGeography()
    industry: str | None = None
    subindustry: str | None = None

    @field_validator("profile_id")
    @classmethod
    def valid_profile_id(cls, value: str) -> str:
        if not _PROFILE_ID.fullmatch(value):
            raise ValueError("profile_id must be a lowercase kebab-case identifier")
        return value


def build_campaign_profile_registry(
    profiles: Iterable[CampaignProfile],
) -> Mapping[str, CampaignProfile]:
    """Build a read-only registry and reject duplicate stable identifiers."""

    registry: dict[str, CampaignProfile] = {}
    for profile in profiles:
        if profile.profile_id in registry:
            raise ValueError(f"Duplicate campaign profile ID: {profile.profile_id}")
        registry[profile.profile_id] = profile
    return MappingProxyType(registry)


DEFAULT_CAMPAIGN_PROFILES: Mapping[str, CampaignProfile] = build_campaign_profile_registry(
    (
        CampaignProfile(
            profile_id="health-v1",
            version="1",
            name="Health",
            campaign_type=CampaignType.HEALTH,
            scoring_profile="health-v1",
            research_profile="health-research-v3",
            shortlist_profile="daily-shortlist-v1",
            default_industry="health",
            target_roles=DEFAULT_TARGET_ROLES,
        ),
        CampaignProfile(
            profile_id="construction-v1",
            version="1",
            name="Construction",
            campaign_type=CampaignType.CONSTRUCTION,
            scoring_profile="construction-v1",
            research_profile="construction-research-v2",
            shortlist_profile="daily-shortlist-v1",
            default_industry="construction",
            target_roles=DEFAULT_TARGET_ROLES,
        ),
        CampaignProfile(
            profile_id="hospitality-v1",
            version="1",
            name="Hospitality",
            campaign_type=CampaignType.HOSPITALITY,
            scoring_profile="hospitality-v1",
            research_profile="hospitality-research-v2",
            shortlist_profile="daily-shortlist-v1",
            default_industry="hospitality",
            target_roles=DEFAULT_TARGET_ROLES,
        ),
    )
)


def get_campaign_profile(profile_id: str) -> CampaignProfile:
    """Resolve a canonical profile ID without silently falling back."""

    try:
        return DEFAULT_CAMPAIGN_PROFILES[profile_id]
    except KeyError as exc:
        raise LookupError(f"Unknown campaign profile: {profile_id}") from exc


def profile_for_campaign_type(kind: CampaignType) -> CampaignProfile:
    """Compatibility resolver for campaigns that only store CampaignType."""

    profile_id = {
        CampaignType.HEALTH: "health-v1",
        CampaignType.CONSTRUCTION: "construction-v1",
        CampaignType.HOSPITALITY: "hospitality-v1",
    }[kind]
    return get_campaign_profile(profile_id)
