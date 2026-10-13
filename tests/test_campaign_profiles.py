from uuid import uuid4

import pytest
from pydantic import ValidationError

from lead_engine.application.discovery import discovery_query_for_profile
from lead_engine.domain.campaign_profiles import (
    DEFAULT_CAMPAIGN_PROFILES,
    CampaignConfiguration,
    CampaignGeography,
    CampaignProfile,
    build_campaign_profile_registry,
    get_campaign_profile,
    profile_for_campaign_type,
)
from lead_engine.domain.contact_research import DEFAULT_TARGET_ROLES
from lead_engine.domain.enums import CampaignType, RoleCategory
from lead_engine.domain.models import Campaign
from lead_engine.domain.policies import DEFAULT_POLICIES
from lead_engine.domain.research_policies import DEFAULT_RESEARCH_POLICIES
from lead_engine.domain.shortlist_policy import V1ShortlistPolicy


def test_registry_has_exact_supported_versioned_profiles() -> None:
    assert set(DEFAULT_CAMPAIGN_PROFILES) == {
        "health-v1",
        "construction-v1",
        "hospitality-v1",
    }
    assert len(DEFAULT_CAMPAIGN_PROFILES) == len(set(DEFAULT_CAMPAIGN_PROFILES))
    with pytest.raises(TypeError):
        DEFAULT_CAMPAIGN_PROFILES["other-v1"] = get_campaign_profile("health-v1")  # type: ignore[index]


def test_profiles_are_immutable_and_unknown_ids_fail_clearly() -> None:
    profile = get_campaign_profile("health-v1")
    with pytest.raises(ValidationError):
        profile.name = "Changed"  # type: ignore[misc]
    with pytest.raises(LookupError, match="Unknown campaign profile"):
        get_campaign_profile("unknown-v1")


@pytest.mark.parametrize(
    ("kind", "profile_id"),
    [
        (CampaignType.HEALTH, "health-v1"),
        (CampaignType.CONSTRUCTION, "construction-v1"),
        (CampaignType.HOSPITALITY, "hospitality-v1"),
    ],
)
def test_campaign_type_compatibility(kind: CampaignType, profile_id: str) -> None:
    assert profile_for_campaign_type(kind).profile_id == profile_id
    assert Campaign(name="Existing", campaign_type=kind).campaign_type == kind


@pytest.mark.parametrize("profile_id", ["health-v1", "construction-v1", "hospitality-v1"])
def test_profiles_reference_current_scoring_research_and_shortlist_policies(
    profile_id: str,
) -> None:
    profile = get_campaign_profile(profile_id)
    assert profile.scoring_profile == DEFAULT_POLICIES[profile.campaign_type].version
    assert profile.research_profile == DEFAULT_RESEARCH_POLICIES[profile.campaign_type].version
    assert profile.shortlist_profile == V1ShortlistPolicy().version


def test_profile_identifiers_are_validated() -> None:
    for fields in (
        {"profile_id": " ", "version": "1", "name": "Health"},
        {"profile_id": "health-v1", "version": " ", "name": "Health"},
        {"profile_id": "Health v1", "version": "1", "name": "Health"},
    ):
        with pytest.raises(ValidationError):
            CampaignProfile(
                campaign_type=CampaignType.HEALTH,
                scoring_profile="health-v1",
                research_profile="health-research-v3",
                shortlist_profile="daily-shortlist-v1",
                **fields,
            )


def test_registry_rejects_duplicate_ids() -> None:
    profile = get_campaign_profile("health-v1")
    with pytest.raises(ValueError, match="Duplicate campaign profile"):
        build_campaign_profile_registry((profile, profile))


def test_health_profile_is_independent_of_city_and_query_uses_configuration() -> None:
    profile = get_campaign_profile("health-v1")
    chiclayo = CampaignConfiguration(
        profile_id=profile.profile_id,
        geography=CampaignGeography(country="Peru", region="Lambayeque", city="Chiclayo"),
    )
    lima = CampaignConfiguration(
        profile_id=profile.profile_id,
        geography=CampaignGeography(country="Peru", region="Lima", city="Lima"),
    )
    chiclayo_query = discovery_query_for_profile(uuid4(), profile, chiclayo)
    lima_query = discovery_query_for_profile(uuid4(), profile, lima)
    assert chiclayo_query.industry == lima_query.industry == "health"
    assert (chiclayo_query.country, chiclayo_query.region, chiclayo_query.city) == (
        "Peru",
        "Lambayeque",
        "Chiclayo",
    )
    assert (lima_query.country, lima_query.region, lima_query.city) == ("Peru", "Lima", "Lima")
    assert (
        profile.scoring_profile,
        profile.research_profile,
        profile.shortlist_profile,
    ) == (
        get_campaign_profile("health-v1").scoring_profile,
        get_campaign_profile("health-v1").research_profile,
        get_campaign_profile("health-v1").shortlist_profile,
    )


def test_configuration_industry_and_subindustry_are_data_not_policy_selection() -> None:
    profile = get_campaign_profile("health-v1")
    config = CampaignConfiguration(
        profile_id=profile.profile_id,
        geography=CampaignGeography(city="Trujillo"),
        industry="private clinics",
        subindustry="diagnostics",
    )
    query = discovery_query_for_profile(uuid4(), profile, config)
    assert query.industry == "private clinics"
    assert config.subindustry == "diagnostics"
    assert profile_for_campaign_type(profile.campaign_type) == profile


def test_discovery_query_derivation_rejects_profile_configuration_mismatch() -> None:
    with pytest.raises(ValueError, match="profile_id"):
        discovery_query_for_profile(
            uuid4(),
            get_campaign_profile("health-v1"),
            CampaignConfiguration(
                profile_id="construction-v1", geography=CampaignGeography(city="Lima")
            ),
        )


def test_target_roles_use_existing_role_categories_and_campaign_types_are_unchanged() -> None:
    profiles = DEFAULT_CAMPAIGN_PROFILES.values()
    assert DEFAULT_TARGET_ROLES == (
        RoleCategory.MARKETING,
        RoleCategory.GENERAL_MANAGEMENT,
        RoleCategory.DIGITAL,
    )
    assert all(set(profile.target_roles) <= set(RoleCategory) for profile in profiles)
    assert all(profile.target_roles == DEFAULT_TARGET_ROLES for profile in profiles)
    assert set(CampaignType) == {
        CampaignType.HEALTH,
        CampaignType.CONSTRUCTION,
        CampaignType.HOSPITALITY,
    }


def test_configuration_does_not_change_profile_policy_references() -> None:
    profile = get_campaign_profile("health-v1")
    construction = get_campaign_profile("construction-v1")
    assert profile.campaign_type == CampaignType.HEALTH
    assert profile.scoring_profile == "health-v1"
    assert profile.research_profile == "health-research-v3"
    assert profile.shortlist_profile == construction.shortlist_profile
    assert profile.scoring_profile != construction.scoring_profile
    assert profile.research_profile != construction.research_profile
