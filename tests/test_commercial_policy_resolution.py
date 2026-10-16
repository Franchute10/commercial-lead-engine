"""Profile-first resolution for validated commercial policy bundles."""

from uuid import uuid4

import pytest

from lead_engine.domain.campaign_profiles import DEFAULT_CAMPAIGN_PROFILES
from lead_engine.domain.commercial_policies import (
    DEFAULT_COMMERCIAL_POLICY_BUNDLES,
    resolve_commercial_policy_bundle,
)
from lead_engine.domain.contact_research import role_points
from lead_engine.domain.enums import CampaignType, RoleCategory
from lead_engine.domain.models import Campaign, Company, Contact, Evidence, Lead, utc_now
from lead_engine.domain.policies import DEFAULT_POLICIES
from lead_engine.domain.research_policies import DEFAULT_RESEARCH_POLICIES
from lead_engine.domain.scoring import ScoringContext
from lead_engine.domain.sectors import create_sector_suggestion

PROFILE_TYPES = {
    "health-v1": CampaignType.HEALTH,
    "construction-v1": CampaignType.CONSTRUCTION,
    "hospitality-v1": CampaignType.HOSPITALITY,
}


def test_registry_is_complete_and_reuses_the_current_versioned_policies() -> None:
    assert set(DEFAULT_COMMERCIAL_POLICY_BUNDLES) == set(PROFILE_TYPES)
    for profile_id, campaign_type in PROFILE_TYPES.items():
        profile = DEFAULT_CAMPAIGN_PROFILES[profile_id]
        bundle = resolve_commercial_policy_bundle(profile_id)
        assert bundle.profile is profile
        assert bundle.scoring is DEFAULT_POLICIES[campaign_type]
        assert bundle.scoring.version == profile.scoring_profile
        assert bundle.research is DEFAULT_RESEARCH_POLICIES[campaign_type]
        assert bundle.research.version == profile.research_profile
        assert bundle.shortlist.version == profile.shortlist_profile
        assert bundle.outreach.version == "outreach-v1"
        for category in (RoleCategory.OWNER, RoleCategory.MEDICAL_DIRECTOR, RoleCategory.DIGITAL):
            assert bundle.contact_role_fit(category, "small_clinic", "Medical Director") == (
                role_points(
                    category,
                    campaign_type,
                    "small_clinic",
                    "Medical Director",
                )
            )
        assert resolve_commercial_policy_bundle(profile_id) is bundle


def test_type_only_legacy_campaign_resolves_without_mutating_campaign() -> None:
    campaign = Campaign(name="Legacy", campaign_type=CampaignType.HEALTH)
    before = campaign.model_dump()
    assert campaign.profile_id == "health-v1"
    assert resolve_commercial_policy_bundle(campaign) is resolve_commercial_policy_bundle(
        "health-v1"
    )
    assert campaign.model_dump() == before

    legacy_row = Campaign.model_construct(
        name="Legacy row",
        campaign_type=CampaignType.CONSTRUCTION,
        profile_id=None,
    )
    assert resolve_commercial_policy_bundle(legacy_row).profile.profile_id == "construction-v1"
    assert legacy_row.profile_id is None


def test_unknown_and_inconsistent_profile_identifiers_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown campaign profile"):
        resolve_commercial_policy_bundle("unknown-v1")

    mismatch = Campaign.model_construct(
        name="Mismatched",
        campaign_type=CampaignType.HEALTH,
        profile_id="construction-v1",
    )
    with pytest.raises(ValueError, match="must match"):
        resolve_commercial_policy_bundle(mismatch)

    unknown = Campaign.model_construct(
        name="Unknown",
        campaign_type=CampaignType.HEALTH,
        profile_id="unknown-v1",
    )
    with pytest.raises(ValueError, match="Unknown campaign profile"):
        resolve_commercial_policy_bundle(unknown)


def test_sector_classification_and_sector_suggestion_do_not_select_policies() -> None:
    unclassified = Campaign(name="Health", campaign_type=CampaignType.HEALTH)
    classified = Campaign(
        name="Health",
        campaign_type=CampaignType.HEALTH,
        sector_id="health",
        subsector_id="private-clinics",
    )
    bundle = resolve_commercial_policy_bundle(unclassified)
    assert resolve_commercial_policy_bundle(classified) is bundle
    create_sector_suggestion("Nueva actividad")
    assert resolve_commercial_policy_bundle(unclassified) is bundle


def _medical_director_score(
    campaign_type: CampaignType, mode: str
) -> tuple[Campaign, int, int, int]:
    company = Company(canonical_name="Clinic")
    if mode == "normal":
        campaign = Campaign(name="Campaign", campaign_type=campaign_type)
    elif mode == "missing":
        campaign = Campaign.model_construct(name="Campaign", campaign_type=campaign_type)
    elif mode == "null":
        campaign = Campaign.model_construct(
            name="Campaign", campaign_type=campaign_type, profile_id=None
        )
    else:
        campaign = Campaign(
            name="Campaign",
            campaign_type=campaign_type,
            profile_id={
                CampaignType.HEALTH: "health-v1",
                CampaignType.CONSTRUCTION: "construction-v1",
                CampaignType.HOSPITALITY: "hospitality-v1",
            }[campaign_type],
        )
    contact = Contact(
        company_id=company.id,
        full_name="Dr. Example",
        role_category=RoleCategory.MEDICAL_DIRECTOR,
    )
    evidence = Evidence(
        company_id=company.id,
        source_id=uuid4(),
        evidence_type="DECISION_MAKER_ACCESS",
        statement="Medical director authority and reachability verified",
        raw_value={
            "contact_id": str(contact.id),
            "authority_confirmed": True,
            "reachable": True,
        },
        confidence=0.95,
        observed_at=utc_now(),
    )
    lead = Lead(company_id=company.id, campaign_id=campaign.id)
    score, _ = DEFAULT_POLICIES[campaign_type].calculate(
        ScoringContext(lead, company, campaign, (evidence,), (contact,), (), utc_now())
    )
    component = next(item for item in score.components if item.criterion == "DECISION_MAKER_ACCESS")
    return campaign, score.total_score, component.points_awarded, component.max_points


@pytest.mark.parametrize(
    "campaign_type,expected_points",
    [
        (CampaignType.HEALTH, 8),
        (CampaignType.CONSTRUCTION, 0),
        (CampaignType.HOSPITALITY, 0),
    ],
)
def test_medical_director_scoring_matches_for_explicit_and_legacy_profiles(
    campaign_type: CampaignType, expected_points: int
) -> None:
    outputs = [
        _medical_director_score(campaign_type, "normal"),
        _medical_director_score(campaign_type, "missing"),
        _medical_director_score(campaign_type, "null"),
        _medical_director_score(campaign_type, "explicit"),
    ]
    scores = [(total, points, maximum) for _, total, points, maximum in outputs]
    assert scores[0] == scores[1] == scores[2] == scores[3]
    assert outputs[0][2] == expected_points
    for campaign, _, _, _ in outputs:
        before = campaign.model_dump()
        resolve_commercial_policy_bundle(campaign)
        assert campaign.model_dump() == before
