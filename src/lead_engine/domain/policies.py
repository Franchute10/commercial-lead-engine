"""Explicit V1 commercial heuristics; every point is attached to an observed signal."""

import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from lead_engine.domain.audit import AuditStatus, FindingType
from lead_engine.domain.commercial import (
    BusinessSignal,
    CommercialEvidence,
    DecisionMakerObservation,
)
from lead_engine.domain.enums import CampaignType, RoleCategory
from lead_engine.domain.models import Evidence, LeadScore, ScoreComponent
from lead_engine.domain.scoring import ScoreSummary, ScoringContext, SignalState, score_band


@dataclass(frozen=True)
class Fact:
    state: SignalState
    value: object = None
    evidence_ids: tuple[UUID, ...] = ()
    note: str = ""

    @property
    def known(self) -> bool:
        return self.state in {SignalState.PRESENT, SignalState.ABSENT}


class Observations:
    def __init__(self, context: ScoringContext, minimum_confidence: float = 0.7) -> None:
        self.context = context
        self.minimum_confidence = minimum_confidence
        eligible = [
            audit
            for audit in context.audits
            if audit.finished_at is not None
            and audit.finished_at <= context.as_of
            and audit.website_url == context.company.website
        ]
        self.audit = (
            max(eligible, key=lambda item: item.finished_at or item.started_at)
            if eligible
            else None
        )
        if (
            self.audit
            and self.audit.finished_at
            and self.audit.finished_at < context.as_of - timedelta(days=30)
        ):
            self.audit = None
        self._cache: dict[str, Fact] = {}

    def _resolve(self, samples: list[tuple[Evidence, object]]) -> Fact:
        refs = tuple(sorted({item.id for item, _ in samples}))
        qualified = [
            (item, value) for item, value in samples if item.confidence >= self.minimum_confidence
        ]
        if not qualified:
            return Fact(
                SignalState.UNKNOWN,
                evidence_ids=refs,
                note="no eligible high-confidence observation",
            )
        values = {json.dumps(value, sort_keys=True, default=str) for _, value in qualified}
        if len(values) == 1:
            item, value = max(
                qualified,
                key=lambda pair: (pair[0].observed_at, pair[0].confidence, str(pair[0].id)),
            )
            return Fact(SignalState.ABSENT if value is False else SignalState.PRESENT, value, refs)
        for candidate, value in sorted(
            qualified, key=lambda pair: pair[0].observed_at, reverse=True
        ):
            differing = [item for item, other in qualified if other != value]
            if all(
                candidate.observed_at > item.observed_at and candidate.confidence >= item.confidence
                for item in differing
            ):
                return Fact(
                    SignalState.ABSENT if value is False else SignalState.PRESENT,
                    value,
                    refs,
                    "conflict resolved by newer equally/higher-confidence evidence",
                )
        return Fact(
            SignalState.UNCERTAIN,
            evidence_ids=refs,
            note="conflicting observations; points withheld",
        )

    def _eligible(self, evidence: Evidence, age_days: int) -> bool:
        return (
            evidence.company_id == self.context.company.id
            and evidence.created_at <= self.context.as_of
            and self.context.as_of - timedelta(days=age_days)
            <= evidence.observed_at
            <= self.context.as_of
        )

    def fact(self, signal: str) -> Fact:
        if signal in self._cache:
            return self._cache[signal]
        age = (
            90
            if signal in {"INSTAGRAM_ACTIVE", "FACEBOOK_ACTIVE", "PAID_ADVERTISING_ACTIVE"}
            else 365
        )
        if signal in FindingType.__members__:
            age = 30
        if signal in {
            "RECENT_EXPANSION",
            "NEW_LOCATION",
            "ACTIVE_HIRING",
            "RECENT_REBRAND",
            "DECISION_MAKER_ACCESS",
        }:
            age = 180
        samples: list[tuple[Evidence, object]] = []
        for item in self.context.evidence:
            if (
                item.evidence_type != signal
                or item.website_audit_id is not None
                or not self._eligible(item, age)
            ):
                continue
            value: object = item.raw_value
            if signal in BusinessSignal.__members__:
                try:
                    validated = CommercialEvidence.model_validate(item.model_dump())
                except ValueError:
                    continue
                if signal == "GOOGLE_RATING":
                    value = Decimal(str(validated.raw_value)).normalize()
                elif signal == "DECISION_MAKER_ACCESS":
                    continue  # Process per contact, not as contradictory company-wide person IDs.
            elif type(value) is not bool:
                continue
            samples.append((item, value))
        if signal == "DECISION_MAKER_ACCESS":
            result = self._decision_maker()
        else:
            samples.extend(self._website_samples(signal))
            result = self._resolve(samples)
        self._cache[signal] = result
        return result

    def _website_samples(self, signal: str) -> list[tuple[Evidence, object]]:
        audit = self.audit
        if audit is None or audit.status not in {AuditStatus.SUCCESS, AuditStatus.NO_WEBSITE}:
            return []
        samples: list[tuple[Evidence, object]] = []
        for item in self.context.evidence:
            if (
                item.website_audit_id != audit.id
                or item.source_id != audit.source_id
                or not self._eligible(item, 30)
            ):
                continue
            if (
                item.evidence_type == "WEBSITE_SIGNAL_COVERAGE"
                and audit.status == AuditStatus.SUCCESS
            ):
                raw = item.raw_value
                if isinstance(raw, dict) and raw.get("version") == "static-homepage-v1":
                    values = raw.get("signals")
                    if isinstance(values, dict) and type(values.get(signal)) is bool:
                        samples.append((item, values[signal]))
            if item.evidence_type == signal:
                if signal == "NO_WEBSITE" and audit.status == AuditStatus.NO_WEBSITE:
                    samples.append((item, True))
                elif signal == "HTTPS_MISSING" and item.raw_value == "http":
                    samples.append((item, True))
                elif signal == "SLOW_RESPONSE" and audit.response_time_ms is not None:
                    samples.append((item, audit.response_time_ms >= 3000))
                elif audit.status == AuditStatus.SUCCESS and signal in FindingType.__members__:
                    samples.append((item, True))
            if signal == "NO_WEBSITE" and item.evidence_type == "WEBSITE_REACHABLE":
                samples.append((item, False))
            if signal == "HTTPS_MISSING" and item.evidence_type == "HTTPS_ENABLED":
                samples.append((item, False))
            if (
                signal == "SLOW_RESPONSE"
                and item.evidence_type == "WEBSITE_REACHABLE"
                and audit.response_time_ms is not None
            ):
                samples.append((item, audit.response_time_ms >= 3000))
        return samples

    def _decision_maker(self) -> Fact:
        contacts = {
            item.id: item
            for item in self.context.contacts
            if item.company_id == self.context.company.id
        }
        groups: dict[UUID, list[tuple[Evidence, object]]] = {}
        for item in self.context.evidence:
            if item.evidence_type != "DECISION_MAKER_ACCESS" or not self._eligible(item, 180):
                continue
            try:
                value = DecisionMakerObservation.model_validate_json(json.dumps(item.raw_value))
            except ValueError:
                continue
            contact = contacts.get(value.contact_id)
            if contact is None:
                continue
            points = 0
            if value.authority_confirmed and value.reachable:
                if contact.role_category in {
                    RoleCategory.OWNER,
                    RoleCategory.FOUNDER,
                    RoleCategory.GENERAL_MANAGEMENT,
                }:
                    points = 10
                elif contact.role_category in {
                    RoleCategory.COMMERCIAL,
                    RoleCategory.MARKETING,
                    RoleCategory.DIGITAL,
                    RoleCategory.CUSTOMER_EXPERIENCE,
                }:
                    points = 8
                elif (
                    contact.role_category == RoleCategory.MEDICAL_DIRECTOR
                    and self.context.campaign.campaign_type == CampaignType.HEALTH
                ):
                    points = 8
                elif contact.role_category == RoleCategory.ADMINISTRATION:
                    points = 4
            groups.setdefault(contact.id, []).append((item, points))
        facts = [self._resolve(samples) for samples in groups.values()]
        refs = tuple(sorted({ref for fact in facts for ref in fact.evidence_ids}))
        known = [fact for fact in facts if fact.known]
        if known:
            return Fact(
                SignalState.PRESENT,
                max(int(str(fact.value)) for fact in known),
                refs,
                "per-contact conflicts resolved; strongest verified reachable role used",
            )
        return Fact(
            SignalState.UNCERTAIN if facts else SignalState.UNKNOWN,
            evidence_ids=refs,
            note="no resolved verified decision-maker access",
        )

    def commercial_anchor(self) -> Fact:
        signals = (
            "HIGH_VALUE_SERVICE",
            "HIGH_VALUE_PRODUCT",
            "MULTIPLE_LOCATIONS",
            "PRIVATE_EVENTS",
            "ECOMMERCE",
            "DISTRIBUTION_NETWORK",
            "B2B_OPERATION",
            "CORPORATE_CLIENTS",
            "EXPORT_ACTIVITY",
        )
        facts = [self.fact(signal) for signal in signals]
        reputation = self.fact("GOOGLE_REVIEW_COUNT")
        refs = tuple(sorted({ref for fact in [*facts, reputation] for ref in fact.evidence_ids}))
        positive = any(fact.known and fact.value is True for fact in facts)
        positive = positive or (
            reputation.known and isinstance(reputation.value, int) and reputation.value >= 10
        )
        return Fact(
            SignalState.PRESENT
            if positive
            else (
                SignalState.ABSENT
                if all(fact.known for fact in [*facts, reputation])
                else SignalState.UNKNOWN
            ),
            positive,
            refs,
            "opportunity requires observed business scale/value or >=10 reviews",
        )


@dataclass(frozen=True)
class Rule:
    signal: str
    points: int
    mode: str = "present"
    gated: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"present", "absent", "reviews", "rating", "decision"}:
            raise ValueError("Unsupported scoring rule mode")


@dataclass(frozen=True)
class Dimension:
    criterion: str
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class V1Policy:
    campaign_type: CampaignType
    version: str
    dimensions: tuple[Dimension, ...]
    bands: tuple[int, int, int, int] = (85, 70, 55, 40)

    def __post_init__(self) -> None:
        if sum(rule.points for dimension in self.dimensions for rule in dimension.rules) != 100:
            raise ValueError("Policy maximums must sum to 100")
        if len({dimension.criterion for dimension in self.dimensions}) != len(self.dimensions):
            raise ValueError("Policy criteria must be unique")
        if any(rule.points <= 0 for dimension in self.dimensions for rule in dimension.rules):
            raise ValueError("Rule weights must be positive")
        score_band(Decimal(0), self.bands)
        if self.bands != (85, 70, 55, 40) and self.version.endswith("-v1"):
            raise ValueError("Custom band thresholds require a distinct policy version")

    def calculate(self, context: ScoringContext) -> tuple[LeadScore, ScoreSummary]:
        if context.campaign.campaign_type != self.campaign_type:
            raise ValueError("Policy does not match campaign")
        observations = Observations(context)
        score_id = uuid4()
        components = []
        known_weight = 0
        for dimension in self.dimensions:
            awarded = Decimal(0)
            refs: set[UUID] = set()
            explanations: list[str] = []
            for rule in dimension.rules:
                fact = observations.fact(rule.signal)
                refs.update(fact.evidence_ids)
                known = fact.known
                amount = Decimal(0)
                gate = observations.commercial_anchor() if rule.gated else None
                if gate:
                    refs.update(gate.evidence_ids)
                    known = known and gate.known
                if known:
                    known_weight += rule.points
                    if gate and gate.value is not True:
                        amount = Decimal(0)
                    elif rule.mode == "absent":
                        amount = (
                            Decimal(rule.points) if fact.state == SignalState.ABSENT else Decimal(0)
                        )
                    elif rule.mode == "reviews":
                        count = int(str(fact.value))
                        fraction = (
                            Decimal(1)
                            if count >= 500
                            else Decimal("0.8")
                            if count >= 200
                            else Decimal("0.5")
                            if count >= 50
                            else Decimal("0.2")
                            if count >= 10
                            else Decimal(0)
                        )
                        amount = (Decimal(rule.points) * fraction).quantize(
                            Decimal(1), rounding=ROUND_HALF_UP
                        )
                    elif rule.mode == "rating":
                        reviews = observations.fact("GOOGLE_REVIEW_COUNT")
                        refs.update(reviews.evidence_ids)
                        if reviews.known and int(str(reviews.value)) >= 10:
                            rating = Decimal(str(fact.value))
                            amount = (
                                Decimal(rule.points)
                                if rating >= Decimal("4.5")
                                else (Decimal(rule.points) / 2).quantize(
                                    Decimal(1), rounding=ROUND_HALF_UP
                                )
                                if rating >= 4
                                else Decimal(0)
                            )
                        else:
                            known_weight -= rule.points
                    elif rule.mode == "decision":
                        amount = Decimal(str(fact.value))
                    elif fact.value is True:
                        amount = Decimal(rule.points)
                awarded += amount
                explanations.append(
                    f"{rule.signal}={fact.state.value}; mode={rule.mode}; "
                    f"value={fact.value}: {amount}/{rule.points}"
                    + ("; business anchor unknown" if gate and not gate.known else "")
                    + (
                        "; business anchor absent; opportunity withheld"
                        if gate and gate.state == SignalState.ABSENT
                        else ""
                    )
                    + (f"; {fact.note}" if fact.note else "")
                )
            components.append(
                ScoreComponent(
                    lead_score_id=score_id,
                    criterion=dimension.criterion,
                    points_awarded=awarded,
                    max_points=Decimal(sum(rule.points for rule in dimension.rules)),
                    explanation=" | ".join(explanations),
                    evidence_ids=tuple(sorted(refs)),
                )
            )
        total = sum((component.points_awarded for component in components), Decimal(0))
        summary = ScoreSummary(
            band=score_band(total, self.bands),
            completeness_percent=known_weight,
            known_weight=known_weight,
            bands=self.bands,
            audit_id=str(observations.audit.id) if observations.audit else None,
            input_cutoff=context.as_of.isoformat(),
        )
        score = LeadScore(
            id=score_id,
            lead_id=context.lead.id,
            total_score=total,
            scoring_version=self.version,
            calculated_at=context.as_of,
            explanation=summary.model_dump_json(),
            components=tuple(components),
        )
        return score, summary


def dimension(name: str, *rules: Rule) -> Dimension:
    return Dimension(name, rules)


def opportunity(signal: str, points: int, mode: str = "absent") -> Rule:
    return Rule(signal, points, mode, True)


DIGITAL = dimension(
    "DIGITAL_ACTIVITY",
    Rule("INSTAGRAM_ACTIVE", 4),
    Rule("FACEBOOK_ACTIVE", 3),
    Rule("PAID_ADVERTISING_ACTIVE", 3),
)
DECISION = dimension("DECISION_MAKER_ACCESS", Rule("DECISION_MAKER_ACCESS", 10, "decision"))
HEALTH_V1 = V1Policy(
    CampaignType.HEALTH,
    "health-v1",
    (
        dimension(
            "COMMERCIAL_VALUE",
            Rule("HIGH_VALUE_SERVICE", 12),
            Rule("CORPORATE_CLIENTS", 4),
            Rule("MULTIPLE_LOCATIONS", 4),
        ),
        DIGITAL,
        dimension(
            "REPUTATION_SIGNAL",
            Rule("GOOGLE_REVIEW_COUNT", 8, "reviews"),
            Rule("GOOGLE_RATING", 2, "rating"),
        ),
        dimension(
            "WEBSITE_OPPORTUNITY",
            opportunity("NO_WEBSITE", 5, "present"),
            opportunity("MOBILE_VIEWPORT_PRESENT", 8),
            opportunity("META_DESCRIPTION_PRESENT", 4),
            opportunity("HTTPS_MISSING", 4, "present"),
            opportunity("SLOW_RESPONSE", 4, "present"),
        ),
        dimension(
            "CONVERSION_OPPORTUNITY",
            opportunity("HAS_RESERVATION_PATH", 8),
            opportunity("CONTACT_FORM_PRESENT", 6),
            opportunity("WHATSAPP_LINK_PRESENT", 6),
        ),
        DECISION,
        dimension("GROWTH_SIGNAL", Rule("RECENT_EXPANSION", 3), Rule("ACTIVE_HIRING", 2)),
    ),
)
CONSTRUCTION_V1 = V1Policy(
    CampaignType.CONSTRUCTION,
    "construction-v1",
    (
        dimension(
            "COMMERCIAL_SCALE",
            Rule("DISTRIBUTION_NETWORK", 8),
            Rule("B2B_OPERATION", 6),
            Rule("HIGH_VALUE_PRODUCT", 6),
        ),
        DIGITAL,
        dimension(
            "CATALOG_OPPORTUNITY",
            opportunity("HAS_PRODUCT_DISCOVERY_PATH", 10),
            opportunity("CATALOG_PRESENT", 5),
        ),
        dimension(
            "QUOTATION_OPPORTUNITY",
            opportunity("HAS_QUOTE_PATH", 12),
            opportunity("CONTACT_FORM_PRESENT", 8),
        ),
        dimension(
            "WEBSITE_OPPORTUNITY",
            opportunity("NO_WEBSITE", 3, "present"),
            opportunity("MOBILE_VIEWPORT_PRESENT", 6),
            opportunity("META_DESCRIPTION_PRESENT", 3),
            opportunity("HTTPS_MISSING", 3, "present"),
        ),
        DECISION,
        dimension(
            "GROWTH_SIGNAL",
            Rule("RECENT_EXPANSION", 4),
            Rule("NEW_LOCATION", 3),
            Rule("ACTIVE_HIRING", 3),
        ),
    ),
)
HOSPITALITY_V1 = V1Policy(
    CampaignType.HOSPITALITY,
    "hospitality-v1",
    (
        dimension(
            "BRAND_ACTIVITY",
            Rule("INSTAGRAM_ACTIVE", 6),
            Rule("FACEBOOK_ACTIVE", 4),
            Rule("PRIVATE_EVENTS", 5),
        ),
        dimension(
            "REPUTATION_SIGNAL",
            Rule("GOOGLE_REVIEW_COUNT", 12, "reviews"),
            Rule("GOOGLE_RATING", 3, "rating"),
        ),
        dimension(
            "RESERVATION_OPPORTUNITY",
            opportunity("HAS_RESERVATION_PATH", 12),
            opportunity("BOOKING_CTA_PRESENT", 8),
        ),
        dimension(
            "WEBSITE_OPPORTUNITY",
            opportunity("NO_WEBSITE", 4, "present"),
            opportunity("MOBILE_VIEWPORT_PRESENT", 8),
            opportunity("META_DESCRIPTION_PRESENT", 4),
            opportunity("HTTPS_MISSING", 4, "present"),
        ),
        dimension(
            "EXPERIENCE_OPPORTUNITY",
            opportunity("HAS_DIRECT_CONTACT_PATH", 8),
            opportunity("MAP_LINK_PRESENT", 4),
            opportunity("BUSINESS_HOURS_PRESENT", 3),
        ),
        DECISION,
        dimension("GROWTH_SIGNAL", Rule("NEW_LOCATION", 3), Rule("RECENT_EXPANSION", 2)),
    ),
)
DEFAULT_POLICIES = {
    policy.campaign_type: policy for policy in (HEALTH_V1, CONSTRUCTION_V1, HOSPITALITY_V1)
}
