"""Versioned research templates: supported anchors plus explicitly observed gaps."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from lead_engine.domain.audit import AuditStatus
from lead_engine.domain.commercial import BusinessSignal
from lead_engine.domain.contact_research import VerificationState
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.policies import Fact, Observations
from lead_engine.domain.research import (
    BriefStatus,
    CommercialBrief,
    CompletenessCategory,
    EvidenceCitation,
    Opportunity,
    OpportunityPriority,
    OpportunityType,
    ResearchContext,
    ResearchPolicy,
)
from lead_engine.domain.scoring import SignalState


@dataclass(frozen=True)
class ResearchRule:
    kind: OpportunityType
    title: str
    anchors: tuple[str, ...]
    gaps: tuple[str, ...]
    strength: int = 2


LABELS: dict[str, str] = {
    "HIGH_VALUE_SERVICE": "High-value services are supported by stored evidence",
    "HIGH_VALUE_PRODUCT": "High-value products are supported by stored evidence",
    "B2B_OPERATION": "B2B operation is explicitly supported",
    "DISTRIBUTION_NETWORK": "A distribution network is explicitly supported",
    "CORPORATE_CLIENTS": "Corporate clients are explicitly supported",
    "PRIVATE_EVENTS": "Private events are explicitly offered",
    "INSTAGRAM_ACTIVE": "An active Instagram presence is supported",
    "MULTIPLE_LOCATIONS": "Multiple locations are explicitly supported",
    "ECOMMERCE": "An ecommerce operation is explicitly supported",
    "HAS_RESERVATION_PATH": "No reservation/appointment path was observed in the inspected scope",
    "BOOKING_CTA_PRESENT": "No booking CTA was observed in the inspected scope",
    "HAS_QUOTE_PATH": "No quotation path was observed in the inspected scope",
    "QUOTE_CTA_PRESENT": "No quotation CTA was observed in the inspected scope",
    "HAS_PRODUCT_DISCOVERY_PATH": "No product discovery path was observed in the inspected scope",
    "HAS_SERVICE_DISCOVERY_PATH": "No service discovery path was observed in the inspected scope",
    "CATALOG_PRESENT": "No catalog/menu link was observed in the inspected scope",
    "HAS_DIRECT_CONTACT_PATH": "No direct contact path was observed in the inspected scope",
    "CONTACT_FORM_PRESENT": "No contact form was observed in the inspected scope",
    "PRIVACY_POLICY_PRESENT": "No privacy-policy link was observed in the inspected scope",
    "META_DESCRIPTION_PRESENT": "No meta description was observed in the inspected scope",
    "MAP_LINK_PRESENT": "No map link was observed in the inspected scope",
    "ECOMMERCE_PRESENT": "No ecommerce path was observed in the inspected scope",
}

RULES: dict[CampaignType, tuple[ResearchRule, ...]] = {
    CampaignType.HEALTH: (
        ResearchRule(
            OpportunityType.APPOINTMENT_CONVERSION,
            "Appointment conversion",
            ("HIGH_VALUE_SERVICE",),
            ("HAS_RESERVATION_PATH", "BOOKING_CTA_PRESENT"),
            3,
        ),
        ResearchRule(
            OpportunityType.SERVICE_DISCOVERY,
            "Service discovery",
            ("HIGH_VALUE_SERVICE",),
            ("HAS_SERVICE_DISCOVERY_PATH",),
        ),
        ResearchRule(
            OpportunityType.CUSTOMER_JOURNEY,
            "Customer journey",
            ("HIGH_VALUE_SERVICE", "CORPORATE_CLIENTS"),
            ("HAS_DIRECT_CONTACT_PATH",),
        ),
    ),
    CampaignType.CONSTRUCTION: (
        ResearchRule(
            OpportunityType.QUOTE_CONVERSION,
            "Quotation conversion",
            ("B2B_OPERATION", "DISTRIBUTION_NETWORK", "HIGH_VALUE_PRODUCT"),
            ("HAS_QUOTE_PATH", "QUOTE_CTA_PRESENT"),
            3,
        ),
        ResearchRule(
            OpportunityType.CATALOG_IMPROVEMENT,
            "Catalog experience",
            ("DISTRIBUTION_NETWORK", "HIGH_VALUE_PRODUCT"),
            ("CATALOG_PRESENT",),
        ),
        ResearchRule(
            OpportunityType.PRODUCT_DISCOVERY,
            "Product discovery",
            ("HIGH_VALUE_PRODUCT", "DISTRIBUTION_NETWORK"),
            ("HAS_PRODUCT_DISCOVERY_PATH",),
        ),
        ResearchRule(
            OpportunityType.B2B_LEAD_CAPTURE,
            "B2B inquiry capture",
            ("B2B_OPERATION", "CORPORATE_CLIENTS"),
            ("HAS_DIRECT_CONTACT_PATH", "CONTACT_FORM_PRESENT"),
        ),
    ),
    CampaignType.HOSPITALITY: (
        ResearchRule(
            OpportunityType.RESERVATION_CONVERSION,
            "Reservation conversion",
            ("PRIVATE_EVENTS", "INSTAGRAM_ACTIVE"),
            ("HAS_RESERVATION_PATH", "BOOKING_CTA_PRESENT"),
            3,
        ),
        ResearchRule(
            OpportunityType.PRIVATE_EVENTS,
            "Private-event inquiry conversion",
            ("PRIVATE_EVENTS",),
            ("HAS_DIRECT_CONTACT_PATH", "CONTACT_FORM_PRESENT"),
        ),
        ResearchRule(
            OpportunityType.BRAND_EXPERIENCE,
            "Brand and menu discovery",
            ("INSTAGRAM_ACTIVE",),
            ("CATALOG_PRESENT",),
        ),
        ResearchRule(
            OpportunityType.CUSTOMER_JOURNEY,
            "Customer journey",
            ("PRIVATE_EVENTS", "MULTIPLE_LOCATIONS"),
            ("HAS_DIRECT_CONTACT_PATH",),
        ),
    ),
}

ANGLES: dict[OpportunityType, str] = {
    OpportunityType.APPOINTMENT_CONVERSION: (
        "Explore how the clinic could simplify the observed digital path "
        "to evaluations and appointments."
    ),
    OpportunityType.RESERVATION_CONVERSION: (
        "Explore how the observed digital experience could simplify reservation requests."
    ),
    OpportunityType.QUOTE_CONVERSION: (
        "Explore how the digital channel could simplify product discovery and quotation requests."
    ),
    OpportunityType.SERVICE_DISCOVERY: (
        "Explore how patients could more easily discover the documented services."
    ),
    OpportunityType.CATALOG_IMPROVEMENT: (
        "Explore how customers could more easily navigate the catalog and "
        "request product information."
    ),
    OpportunityType.PRODUCT_DISCOVERY: (
        "Explore how buyers could more easily discover the documented products."
    ),
    OpportunityType.B2B_LEAD_CAPTURE: (
        "Explore how the digital channel could simplify inquiries from business buyers."
    ),
    OpportunityType.PRIVATE_EVENTS: (
        "Explore how the documented private-event offer could connect to a clearer inquiry path."
    ),
    OpportunityType.BRAND_EXPERIENCE: (
        "Explore how the digital experience could connect the documented "
        "brand presence and menu discovery."
    ),
    OpportunityType.CUSTOMER_JOURNEY: (
        "Explore how the observed digital journey could simplify customer inquiries."
    ),
    OpportunityType.DIGITAL_TRUST: (
        "Explore whether clearer digital trust information could support customer confidence."
    ),
    OpportunityType.CONTACTABILITY: (
        "Explore how customers could reach the business through a clearer documented contact path."
    ),
    OpportunityType.ECOMMERCE: (
        "Explore how the documented ecommerce offer could connect to a "
        "clearer digital purchase path."
    ),
    OpportunityType.LOCAL_DISCOVERY: (
        "Explore how customers could more easily find the documented business locations."
    ),
    OpportunityType.MULTI_LOCATION_EXPERIENCE: (
        "Explore how customers could navigate the documented locations and contact paths."
    ),
    OpportunityType.NO_CLEAR_OPPORTUNITY: (
        "Clarify the business goals and missing evidence before proposing a commercial improvement."
    ),
}


@dataclass(frozen=True)
class V1ResearchPolicy:
    campaign_type: CampaignType

    @property
    def version(self) -> str:
        return f"{self.campaign_type.value.lower()}-research-v1"

    def generate(self, context: ResearchContext) -> CommercialBrief:
        snapshot = context.observations
        obs = Observations(snapshot)
        facts: dict[str, Fact] = {}

        def fact(signal: str) -> Fact:
            if signal not in facts:
                facts[signal] = obs.fact(signal)
            return facts[signal]

        opportunities = []

        def add_rule(rule: ResearchRule) -> None:
            anchors = [
                (signal, fact(signal))
                for signal in rule.anchors
                if fact(signal).state == SignalState.PRESENT and fact(signal).value is True
            ]
            if not anchors:
                return
            # The aggregate path takes precedence; a positive path prevents an absence claim.
            gap = None
            for signal in rule.gaps:
                resolved = fact(signal)
                if resolved.known:
                    if resolved.state == SignalState.ABSENT:
                        gap = (signal, resolved)
                    break
                if resolved.state == SignalState.UNCERTAIN:
                    break
            if gap is None:
                return
            refs = {eid for _, value in anchors for eid in value.evidence_ids}
            refs.update(gap[1].evidence_ids)
            if len(refs) < 2:
                return
            opportunities.append(
                Opportunity(
                    opportunity_type=rule.kind,
                    title=rule.title,
                    reasons=tuple([LABELS[signal] for signal, _ in anchors] + [LABELS[gap[0]]]),
                    supporting_evidence_ids=tuple(sorted(refs)),
                    strength=rule.strength,
                )
            )

        for rule in RULES[self.campaign_type]:
            add_rule(rule)
        # Reputation is an alternative commercial anchor, never a stand-alone opportunity.
        count = fact("GOOGLE_REVIEW_COUNT")
        rating = fact("GOOGLE_RATING")
        strong_reputation = (
            count.known
            and type(count.value) is int
            and count.value >= 100
            and rating.known
            and isinstance(rating.value, Decimal)
            and rating.value >= Decimal("4.3")
        )
        if strong_reputation:
            kind = {
                CampaignType.HEALTH: OpportunityType.APPOINTMENT_CONVERSION,
                CampaignType.CONSTRUCTION: OpportunityType.QUOTE_CONVERSION,
                CampaignType.HOSPITALITY: OpportunityType.RESERVATION_CONVERSION,
            }[self.campaign_type]
            gap_signal = (
                "HAS_QUOTE_PATH"
                if self.campaign_type == CampaignType.CONSTRUCTION
                else "HAS_RESERVATION_PATH"
            )
            gap = fact(gap_signal)
            existing = next(
                (
                    index
                    for index, item in enumerate(opportunities)
                    if item.opportunity_type == kind
                ),
                None,
            )
            reputation_reason = (
                "Stored rating >=4.3 and review count >=100 support a reputation signal"
            )
            if existing is not None:
                current_opportunity = opportunities[existing]
                opportunities[existing] = Opportunity.model_validate(
                    {
                        **current_opportunity.model_dump(),
                        "reasons": current_opportunity.reasons + (reputation_reason,),
                        "supporting_evidence_ids": tuple(
                            sorted(
                                set(
                                    current_opportunity.supporting_evidence_ids
                                    + count.evidence_ids
                                    + rating.evidence_ids
                                )
                            )
                        ),
                    }
                )
            elif gap.state == SignalState.ABSENT:
                refs = tuple(
                    sorted(set(count.evidence_ids + rating.evidence_ids + gap.evidence_ids))
                )
                opportunities.insert(
                    0,
                    Opportunity(
                        opportunity_type=kind,
                        title=kind.value.replace("_", " ").title(),
                        reasons=(reputation_reason, LABELS[gap_signal]),
                        supporting_evidence_ids=refs,
                        strength=3,
                    ),
                )
        campaign_anchors = tuple(
            dict.fromkeys(signal for rule in RULES[self.campaign_type] for signal in rule.anchors)
        )
        for rule in (
            ResearchRule(
                OpportunityType.DIGITAL_TRUST,
                "Digital trust information",
                campaign_anchors,
                ("PRIVACY_POLICY_PRESENT",),
                1,
            ),
            ResearchRule(
                OpportunityType.CONTACTABILITY,
                "Contactability",
                campaign_anchors,
                ("HAS_DIRECT_CONTACT_PATH",),
                2,
            ),
            ResearchRule(
                OpportunityType.ECOMMERCE,
                "Ecommerce journey",
                ("ECOMMERCE",),
                ("ECOMMERCE_PRESENT",),
                2,
            ),
            ResearchRule(
                OpportunityType.LOCAL_DISCOVERY,
                "Local discovery",
                ("MULTIPLE_LOCATIONS",),
                ("MAP_LINK_PRESENT",),
                1,
            ),
            ResearchRule(
                OpportunityType.MULTI_LOCATION_EXPERIENCE,
                "Multi-location contact journey",
                ("MULTIPLE_LOCATIONS",),
                ("HAS_DIRECT_CONTACT_PATH",),
                2,
            ),
        ):
            add_rule(rule)
        # Domain order is the deterministic tie-break; strength determines precedence.
        opportunities.sort(key=lambda item: -item.strength)
        primary = opportunities[0] if opportunities else None
        expansion = fact("RECENT_EXPANSION")
        if primary and expansion.state == SignalState.PRESENT and expansion.value is True:
            primary = Opportunity.model_validate(
                {
                    **primary.model_dump(),
                    "reasons": primary.reasons
                    + ("Recent expansion is supported; confirm its commercial implications",),
                    "supporting_evidence_ids": tuple(
                        sorted(set(primary.supporting_evidence_ids + expansion.evidence_ids))
                    ),
                }
            )
        secondary = tuple(opportunities[1:4])
        selected = ((primary,) if primary else ()) + secondary
        refs = tuple(
            sorted({eid for opportunity in selected for eid in opportunity.supporting_evidence_ids})
        )
        now = snapshot.as_of
        score = context.latest_score
        current_score = bool(score and now - timedelta(days=30) <= score.calculated_at <= now)
        if score and any(
            e.created_at > score.calculated_at
            for e in snapshot.evidence
            if e.evidence_type in BusinessSignal.__members__
            and e.evidence_type != "COMMERCIAL_NOTE"
            and e.created_at <= now
        ):
            current_score = False
        if (
            score
            and obs.audit
            and obs.audit.finished_at
            and obs.audit.finished_at > score.calculated_at
        ):
            current_score = False
        supported_contacts = tuple(
            c for c in context.recommendations if c.verification == VerificationState.SUPPORTED
        )
        available = bool(supported_contacts)
        business_known = [fact(signal) for signal in campaign_anchors if fact(signal).known]
        business_positive = [
            item
            for item in business_known
            if item.state == SignalState.PRESENT and item.value is True
        ]
        website_known = bool(
            obs.audit and obs.audit.status in {AuditStatus.SUCCESS, AuditStatus.NO_WEBSITE}
        )
        basics = (
            int(bool(snapshot.company.canonical_name))
            + int(bool(snapshot.company.industry))
            + int(bool(snapshot.company.city and snapshot.company.country))
        )
        categories = (
            CompletenessCategory(
                category="company_basics",
                maximum_points=15,
                awarded_points=basics * 5,
                explanation="5 each: name; industry; city and country",
            ),
            CompletenessCategory(
                category="website_audit",
                maximum_points=20,
                awarded_points=20 if website_known else 0,
                explanation="20 for current successful audit or explicitly recorded no website",
                evidence_ids=tuple(
                    sorted(
                        e.id
                        for e in snapshot.evidence
                        if obs.audit
                        and e.website_audit_id == obs.audit.id
                        and e.source_id == obs.audit.source_id
                        and e.observed_at <= now
                        and e.created_at <= now
                    )
                )
                if website_known
                else (),
            ),
            CompletenessCategory(
                category="commercial_signals",
                maximum_points=25,
                awarded_points=25 * len(business_known) // len(campaign_anchors),
                explanation=(
                    "25 × known campaign anchors / all campaign anchors, rounded "
                    "down; explicit false is known"
                ),
                evidence_ids=tuple(
                    sorted({eid for item in business_known for eid in item.evidence_ids})
                ),
            ),
            CompletenessCategory(
                category="reputation",
                maximum_points=10,
                awarded_points=5 * int(count.known) + 5 * int(rating.known),
                explanation="5 each for known review count and rating",
                evidence_ids=tuple(
                    sorted(
                        {eid for item in (count, rating) if item.known for eid in item.evidence_ids}
                    )
                ),
            ),
            CompletenessCategory(
                category="contact_research",
                maximum_points=20,
                awarded_points=20 if available else 10 if context.recommendations else 0,
                explanation=(
                    "20 for a current supported person; 10 for stale/conflicted "
                    "supported history; role fallback earns zero"
                ),
                evidence_ids=tuple(
                    sorted({eid for c in context.recommendations for eid in c.evidence_ids})
                ),
            ),
            CompletenessCategory(
                category="score_available",
                maximum_points=10,
                awarded_points=10 if current_score else 5 if score else 0,
                explanation="10 current score; 5 historical/outdated score; 0 missing",
            ),
        )
        completeness = sum(c.awarded_points for c in categories)
        ready = bool(
            primary
            and current_score
            and website_known
            and len(business_positive) >= 2
            and completeness >= 70
        )
        status = (
            BriefStatus.READY
            if ready
            else BriefStatus.PARTIAL
            if primary
            else BriefStatus.INSUFFICIENT_DATA
        )
        priority = OpportunityPriority.NONE
        if primary:
            priority = OpportunityPriority.LOW
            if current_score and score and score.total_score >= 55 and completeness >= 50:
                priority = OpportunityPriority.MEDIUM
            if (
                current_score
                and score
                and score.total_score >= 70
                and completeness >= 70
                and primary.strength == 3
                and available
            ):
                priority = OpportunityPriority.HIGH
        missing = []
        if not snapshot.company.industry:
            missing.append("Industry unknown")
        if not snapshot.company.city:
            missing.append("City unknown")
        if not snapshot.company.country:
            missing.append("Country unknown")
        for signal, label in (
            ("PAID_ADVERTISING_ACTIVE", "Paid advertising activity unknown"),
            ("GOOGLE_REVIEW_COUNT", "Google review count unknown"),
            ("GOOGLE_RATING", "Google rating unknown"),
            ("RECENT_EXPANSION", "Recent expansion activity unknown"),
            ("MULTIPLE_LOCATIONS", "Number of locations unknown"),
            ("RESERVATION_PROVIDER_PRESENT", "Booking provider unknown"),
        ):
            item = fact(signal)
            if not item.known:
                missing.append(label)
        for signal in campaign_anchors:
            if not fact(signal).known:
                missing.append(f"{signal.replace('_', ' ').title()} unknown")
        if not website_known:
            missing.append("No current successful website audit or explicit no-website record")
        if not current_score:
            missing.append("Current commercial lead score unavailable")
        if not available:
            missing.append("No current supported decision maker; research the suggested roles")
        if not any(c.role_category.value in {"OWNER", "FOUNDER"} for c in supported_contacts):
            missing.append("Owner/founder not identified by current contact evidence")
        warnings = [
            "Human commercial judgment and approval remain required before outreach",
            (
                "Static website observations describe the inspected scope, not "
                "the full site or conversion performance"
            ),
        ]
        if not current_score and score:
            warnings.append("Latest score is historical/outdated; rescore before prioritizing")
        if any(c.verification != VerificationState.SUPPORTED for c in context.recommendations):
            warnings.append(
                "Some recommended contacts require role reconfirmation or conflict review"
            )
        for signal, item in facts.items():
            if item.state == SignalState.UNCERTAIN:
                warnings.append(f"{signal}: conflicting evidence; no absence/presence claim used")
            elif item.note and item.known:
                warnings.append(f"{signal}: {item.note}")
        if obs.audit and obs.audit.status not in {AuditStatus.SUCCESS, AuditStatus.NO_WEBSITE}:
            warnings.append(
                "Latest website audit did not complete successfully; missing "
                "findings are not negative evidence"
            )
        summary = snapshot.company.canonical_name[:150]
        if snapshot.company.industry:
            summary += f"; industry: {snapshot.company.industry[:120]}"
        location = ", ".join(v for v in (snapshot.company.city, snapshot.company.country) if v)
        if location:
            summary += f"; location: {location[:120]}"
        if snapshot.company.website:
            summary += f"; recorded website: {snapshot.company.website[:200]}"
        summaries = [
            LABELS[signal]
            for signal in campaign_anchors
            if fact(signal).state == SignalState.PRESENT and fact(signal).value is True
        ]

        if summaries:
            summary += ". " + "; ".join(summaries[:2])
        if strong_reputation:
            summary += ". Stored reputation: rating >=4.3 and >=100 reviews"
        required = set(refs)
        required.update(eid for c in context.recommendations for eid in c.evidence_ids)
        required.update(eid for category in categories for eid in category.evidence_ids)
        sources = {source.id: source for source in context.sources}
        citations = []
        for e in sorted(snapshot.evidence, key=lambda e: str(e.id)):
            if e.id in required:
                source = sources.get(e.source_id)
                if source is None:
                    raise ValueError("Referenced evidence has no stored source")
                citations.append(
                    EvidenceCitation(
                        evidence_id=e.id,
                        source_id=source.id,
                        source_url=source.url,
                        source_title=source.title,
                        evidence_type=e.evidence_type,
                        statement=e.statement,
                        observed_at=e.observed_at,
                    )
                )
        return CommercialBrief(
            lead_id=snapshot.lead.id,
            company_id=snapshot.company.id,
            campaign_id=snapshot.campaign.id,
            generated_at=now,
            research_version=self.version,
            company_name=snapshot.company.canonical_name,
            campaign_name=snapshot.campaign.name,
            company_summary=summary,
            opportunity_type=primary.opportunity_type
            if primary
            else OpportunityType.NO_CLEAR_OPPORTUNITY,
            opportunity_priority=priority,
            primary_opportunity=primary,
            secondary_opportunities=secondary,
            supporting_evidence_ids=refs,
            evidence_citations=tuple(citations),
            decision_maker_recommendations=context.recommendations,
            target_roles=context.target_roles if not context.recommendations else (),
            suggested_contact_angle=ANGLES[
                primary.opportunity_type if primary else OpportunityType.NO_CLEAR_OPPORTUNITY
            ],
            missing_information=tuple(dict.fromkeys(missing)),
            warnings=tuple(dict.fromkeys(warnings)),
            data_completeness=completeness,
            completeness_categories=categories,
            latest_lead_score=float(score.total_score) if score else None,
            lead_score_id=score.id if score else None,
            score_band=context.score_band,
            score_calculated_at=score.calculated_at if score else None,
            website_audit_id=obs.audit.id if obs.audit else None,
            website_audit_status=obs.audit.status if obs.audit else None,
            score_current=current_score,
            decision_maker_available=available,
            pipeline_status=snapshot.lead.status,
            status=status,
        )


DEFAULT_RESEARCH_POLICIES: dict[CampaignType, ResearchPolicy] = {
    kind: V1ResearchPolicy(kind) for kind in CampaignType
}
