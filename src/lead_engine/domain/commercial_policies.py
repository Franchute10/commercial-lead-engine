"""Resolve validated commercial policy bundles from versioned campaign profiles."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from lead_engine.domain.campaign_profiles import (
    DEFAULT_CAMPAIGN_PROFILES,
    CampaignProfile,
    get_campaign_profile,
    resolve_campaign_profile,
)
from lead_engine.domain.contact_research import role_points_for_profile
from lead_engine.domain.enums import RoleCategory
from lead_engine.domain.models import Campaign
from lead_engine.domain.outreach import OutreachPolicy
from lead_engine.domain.outreach_policy import V1OutreachPolicy
from lead_engine.domain.policies import DEFAULT_POLICIES
from lead_engine.domain.research import ResearchPolicy
from lead_engine.domain.research_policies import DEFAULT_RESEARCH_POLICIES
from lead_engine.domain.scoring import CampaignScoringPolicy
from lead_engine.domain.shortlist import ShortlistPolicy
from lead_engine.domain.shortlist_policy import V1ShortlistPolicy

ContactRoleFit = Callable[[RoleCategory, str | None, str], int]


class VersionedPolicy(Protocol):
    @property
    def version(self) -> str: ...


@dataclass(frozen=True)
class CommercialPolicyBundle:
    """Policy implementations selected for one validated, immutable profile."""

    profile: CampaignProfile
    scoring: CampaignScoringPolicy
    research: ResearchPolicy
    contact_role_fit: ContactRoleFit
    shortlist: ShortlistPolicy
    outreach: OutreachPolicy


def _by_version[T: VersionedPolicy](policies: Iterable[T]) -> dict[str, T]:
    registry: dict[str, T] = {}
    for policy in policies:
        version = str(policy.version)
        if version in registry:
            raise ValueError(f"Duplicate commercial policy version: {version}")
        registry[version] = policy
    return registry


def build_commercial_policy_registry(
    profiles: Iterable[CampaignProfile],
    scoring_policies: Iterable[CampaignScoringPolicy],
    research_policies: Iterable[ResearchPolicy],
    shortlist_policies: Iterable[ShortlistPolicy],
    outreach_policies: Iterable[OutreachPolicy],
) -> Mapping[str, CommercialPolicyBundle]:
    """Bind existing policy definitions to the profile references that name them."""

    scoring = _by_version(scoring_policies)
    research = _by_version(research_policies)
    shortlist = _by_version(shortlist_policies)
    outreach = _by_version(outreach_policies)
    registry: dict[str, CommercialPolicyBundle] = {}
    for profile in profiles:
        if profile.profile_id in registry:
            raise ValueError(f"Duplicate commercial policy profile: {profile.profile_id}")
        try:

            def contact_role_fit(
                category: RoleCategory,
                context: str | None,
                title: str,
                *,
                profile_id: str = profile.profile_id,
            ) -> int:
                return role_points_for_profile(category, profile_id, context, title)

            bundle = CommercialPolicyBundle(
                profile=profile,
                scoring=scoring[profile.scoring_profile],
                research=research[profile.research_profile],
                contact_role_fit=contact_role_fit,
                shortlist=shortlist[profile.shortlist_profile],
                outreach=outreach["outreach-v1"],
            )
        except KeyError as exc:
            raise ValueError(
                f"Profile {profile.profile_id} references an unregistered commercial policy: {exc}"
            ) from exc
        if (
            bundle.scoring.campaign_type != profile.campaign_type
            or bundle.research.campaign_type != profile.campaign_type
        ):
            raise ValueError(f"Profile {profile.profile_id} has inconsistent policy types")
        registry[profile.profile_id] = bundle
    return MappingProxyType(registry)


DEFAULT_COMMERCIAL_POLICY_BUNDLES = build_commercial_policy_registry(
    DEFAULT_CAMPAIGN_PROFILES.values(),
    DEFAULT_POLICIES.values(),
    DEFAULT_RESEARCH_POLICIES.values(),
    (V1ShortlistPolicy(),),
    (V1OutreachPolicy(),),
)


def resolve_commercial_policy_bundle(
    profile_or_campaign: str | Campaign,
) -> CommercialPolicyBundle:
    """Resolve by profile ID, validating type compatibility for Campaign inputs."""

    if isinstance(profile_or_campaign, Campaign):
        profile = resolve_campaign_profile(
            profile_or_campaign.profile_id or None, profile_or_campaign.campaign_type
        )
    else:
        try:
            profile = get_campaign_profile(profile_or_campaign)
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
    try:
        return DEFAULT_COMMERCIAL_POLICY_BUNDLES[profile.profile_id]
    except KeyError as exc:
        raise ValueError(
            f"No commercial policy bundle registered for {profile.profile_id}"
        ) from exc


__all__ = [
    "CommercialPolicyBundle",
    "DEFAULT_COMMERCIAL_POLICY_BUNDLES",
    "build_commercial_policy_registry",
    "resolve_commercial_policy_bundle",
]
