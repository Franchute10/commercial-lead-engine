"""Explicit 100-point policy and conservative timing; recommendations never execute."""

from dataclasses import dataclass
from datetime import timedelta

from lead_engine.domain.audit import AuditStatus
from lead_engine.domain.commercial import BusinessSignal
from lead_engine.domain.contact_research import (
    ContactRecommendation,
    PublicChannel,
    VerificationState,
)
from lead_engine.domain.enums import InteractionType, LeadStatus
from lead_engine.domain.identity import normalize_name
from lead_engine.domain.models import LeadInteraction
from lead_engine.domain.research import BriefStatus, OpportunityPriority, OpportunityType
from lead_engine.domain.scoring import ScoreSummary, score_band
from lead_engine.domain.shortlist import (
    NextAction,
    PriorityComponent,
    ShortlistContext,
    ShortlistDecision,
    ShortlistFilters,
    ShortlistItem,
    ShortlistSettings,
    SuppressionReason,
)

CLOSED = {
    LeadStatus.WON: SuppressionReason.WON,
    LeadStatus.LOST: SuppressionReason.LOST,
    LeadStatus.ARCHIVED: SuppressionReason.ARCHIVED,
    LeadStatus.REJECTED: SuppressionReason.PIPELINE_NOT_ACTIONABLE,
}
INTERNAL_STAGES = {LeadStatus.RESPONDED, LeadStatus.MEETING}


@dataclass(frozen=True)
class V1ShortlistPolicy:
    version: str = "daily-shortlist-v1"

    def evaluate(
        self, c: ShortlistContext, f: ShortlistFilters, s: ShortlistSettings
    ) -> ShortlistDecision:
        now = c.as_of
        score = c.score
        brief = c.brief
        warnings = ["Human review and approval required; no recommended action is executed"]
        suppressed = []
        reasons = []

        def suppress(reason: SuppressionReason, text: str) -> None:
            if reason not in suppressed:
                suppressed.append(reason)
            reasons.append(text)

        total = float(score.total_score) if score else None
        band = None
        score_completeness = None
        if score:
            try:
                summary = ScoreSummary.model_validate_json(score.explanation or "")
                band = score_band(score.total_score, summary.bands)
                score_completeness = summary.completeness_percent
            except ValueError:
                band = score_band(score.total_score, (85, 70, 55, 40))
                warnings.append("Historical score completeness unavailable")
        score_fresh = bool(
            score and now - timedelta(days=s.score_freshness_days) <= score.calculated_at <= now
        )
        if score and any(
            e.created_at > score.calculated_at and e.created_at <= now
            for e in c.evidence
            if e.evidence_type in BusinessSignal.__members__
            and e.evidence_type != "COMMERCIAL_NOTE"
        ):
            score_fresh = False
            warnings.append("Commercial evidence changed after scoring; rescore required")
        eligible_audits = [
            a
            for a in c.audits
            if a.finished_at
            and a.finished_at <= now
            and a.created_at <= now
            and a.website_url == c.company.website
        ]
        audit = (
            max(eligible_audits, key=lambda a: (a.finished_at or a.started_at, str(a.id)))
            if eligible_audits
            else None
        )
        audit_fresh = bool(
            audit
            and audit.finished_at
            and now - audit.finished_at <= timedelta(days=s.audit_freshness_days)
            and audit.status in {AuditStatus.SUCCESS, AuditStatus.NO_WEBSITE}
        )
        if score and audit and audit.finished_at and audit.finished_at > score.calculated_at:
            score_fresh = False
            warnings.append("Website audit changed after scoring; rescore required")
        brief_fresh = bool(
            brief
            and now - timedelta(days=s.brief_freshness_days) <= brief.generated_at <= now
            and score
            and brief.lead_score_id == score.id
        )
        if brief and any(
            e.created_at > brief.generated_at and e.created_at <= now
            for e in c.evidence
            if e.evidence_type in BusinessSignal.__members__
        ):
            brief_fresh = False
            warnings.append("Commercial evidence changed after research; regenerate brief")
        if brief and audit and brief.website_audit_id != audit.id:
            brief_fresh = False
        usable_research = bool(
            brief_fresh and score_fresh and audit_fresh and brief and brief.score_current
        )
        if not score_fresh:
            warnings.append("Score missing, outdated or beyond the freshness window")
        if not brief_fresh:
            warnings.append("Research brief missing, stale or not matched to the latest score")
        if not audit_fresh:
            warnings.append("No current successful audit or explicit no-website record")
        if c.lead.status in CLOSED:
            suppress(CLOSED[c.lead.status], f"Pipeline {c.lead.status.value} is excluded")
        minimum = f.minimum_lead_score if f.minimum_lead_score is not None else s.minimum_lead_score
        if total is None or total < minimum:
            suppress(SuppressionReason.LOW_SCORE, f"Latest score does not reach required {minimum}")
        if score and now - score.calculated_at > timedelta(days=s.maximum_score_age_days):
            suppress(
                SuppressionReason.STALE_DATA,
                "Score exceeds maximum age; refresh before daily prioritization",
            )
        if brief and brief.status == BriefStatus.INSUFFICIENT_DATA:
            suppress(
                SuppressionReason.INSUFFICIENT_RESEARCH,
                "Brief cannot responsibly identify an opportunity",
            )
        if brief and brief.primary_opportunity is None:
            suppress(
                SuppressionReason.NO_CLEAR_OPPORTUNITY, "No clear supported commercial opportunity"
            )
        completeness = brief.data_completeness if brief else 0
        if completeness < f.minimum_research_completeness:
            suppress(
                SuppressionReason.INSUFFICIENT_RESEARCH,
                "Research completeness is below the requested minimum",
            )
        if f.pipeline_status is not None and c.lead.status != f.pipeline_status:
            suppress(
                SuppressionReason.FILTER_MISMATCH,
                "Pipeline filter does not match current lead state",
            )
        if f.opportunity_type is not None and (
            brief is None or brief.opportunity_type != f.opportunity_type
        ):
            suppress(
                SuppressionReason.FILTER_MISMATCH, "Opportunity filter does not match latest brief"
            )
        active = [r for r in c.suppressions if r.active_at(now)]
        for record in active:
            suppress(
                SuppressionReason.MANUAL_SUPPRESSION,
                f"Manual suppression until {record.expires_at.isoformat()}: {record.reason}",
            )
        interactions = sorted(
            [i for i in c.interactions if i.occurred_at <= now],
            key=lambda i: (i.occurred_at, str(i.id)),
        )
        last = interactions[-1] if interactions else None
        outbound = [i for i in interactions if i.interaction_type in s.cooldowns]
        cooldowns = [
            (i, i.occurred_at + timedelta(days=s.cooldowns[i.interaction_type])) for i in outbound
        ]
        eligible = max((date for _, date in cooldowns), default=None)
        if eligible and eligible > now and c.lead.status not in INTERNAL_STAGES:
            suppress(
                SuppressionReason.RECENT_CONTACT,
                f"Outbound cooldown active until {eligible.isoformat()}",
            )
        elif eligible and eligible > now:
            warnings.append(
                "Outbound cooldown remains active; only internal preparation is recommended"
            )
        future = any(i.occurred_at > now for i in c.interactions)
        if future:
            warnings.append("Future-dated interaction records require manual review")
        contact, channel = self._contact(c, s)
        if contact:
            reasons.append(f"Current {contact.verification.value} contact; fit {contact.fit_score}")
            warnings.extend(contact.warnings)
        elif c.target_roles:
            reasons.append("Only target roles are available; identify a supported person")
        else:
            reasons.append("No current supported contact or target role is available")
        if channel:
            reasons.append(
                f"Public {channel.channel_type} selected by configured channel preference"
            )
        if any(record.lead_id != c.lead.id for record in outbound):
            reasons.append(
                "Outreach recorded in another campaign at the same company also constrains timing"
            )
        if outbound:
            reasons.append(
                "Prior outreach exists; first-contact actions will not be repeated blindly"
            )
        else:
            reasons.append("No recorded outbound outreach")
        commercial = (
            30
            if total is not None and total >= 85
            else 25
            if total is not None and total >= 70
            else 17
            if total is not None and total >= 55
            else 8
            if total is not None and total >= 40
            else 0
        )
        readiness = 0
        if usable_research and brief:
            readiness = (
                20
                if brief.status == BriefStatus.READY and completeness >= 80
                else 16
                if brief.status == BriefStatus.READY and completeness >= 70
                else 10
                if brief.status == BriefStatus.PARTIAL and completeness >= 50
                else 5
                if brief.status == BriefStatus.PARTIAL
                else 0
            )
        actionability = (
            (20 if contact.fit_score >= 85 else 16 if contact.fit_score >= 70 else 10)
            if contact and channel
            else 8
            if contact
            else 4
            if c.target_roles
            else 0
        )
        opportunity = (
            {
                OpportunityPriority.HIGH: 15,
                OpportunityPriority.MEDIUM: 10,
                OpportunityPriority.LOW: 4,
                OpportunityPriority.NONE: 0,
            }.get(brief.opportunity_priority, 0)
            if brief and brief_fresh and score_fresh
            else 0
        )
        timing = (
            10
            if c.lead.status
            in {
                LeadStatus.QUALIFIED,
                LeadStatus.READY_FOR_OUTREACH,
                LeadStatus.RESPONDED,
                LeadStatus.MEETING,
            }
            else 6
            if c.lead.status
            in {LeadStatus.DISCOVERED, LeadStatus.QUALIFYING, LeadStatus.READY_FOR_RESEARCH}
            else 8
            if c.lead.status in {LeadStatus.CONTACTED, LeadStatus.PROPOSAL}
            else 0
        )
        if eligible and eligible > now and c.lead.status not in INTERNAL_STAGES:
            timing = 0
        freshness = (
            (2 if score_fresh else 0) + (2 if brief_fresh else 0) + (1 if audit_fresh else 0)
        )
        components = (
            PriorityComponent(
                dimension="COMMERCIAL_POTENTIAL",
                points=commercial,
                maximum_points=30,
                explanation="Score bands: >=85 30; >=70 25; >=55 17; >=40 8; otherwise 0",
            ),
            PriorityComponent(
                dimension="RESEARCH_READINESS",
                points=readiness,
                maximum_points=20,
                explanation=(
                    "Current aligned score/brief/audit required; READY >=80 20, >=70 "
                    "16; PARTIAL >=50 10, lower 5"
                ),
            ),
            PriorityComponent(
                dimension="CONTACT_ACTIONABILITY",
                points=actionability,
                maximum_points=20,
                explanation=(
                    "Supported/verified contact + published channel: fit >=85 20, "
                    ">=70 16, lower 10; contact only 8; role fallback 4"
                ),
            ),
            PriorityComponent(
                dimension="OPPORTUNITY_STRENGTH",
                points=opportunity,
                maximum_points=15,
                explanation=(
                    "Aligned fresh brief and current score: HIGH 15, MEDIUM 10, LOW 4, NONE 0"
                ),
            ),
            PriorityComponent(
                dimension="PIPELINE_TIMING",
                points=timing,
                maximum_points=10,
                explanation=(
                    "Qualified/outreach-ready/responded/meeting 10; eligible "
                    "contacted/proposal 8; early stages 6; cooldown/closed 0"
                ),
            ),
            PriorityComponent(
                dimension="FRESHNESS",
                points=freshness,
                maximum_points=5,
                explanation=(
                    "Current score 2 + aligned current brief 2 + current "
                    "successful/no-website audit 1"
                ),
            ),
        )
        action = self._action(c, usable_research, contact, channel, outbound, future)
        if suppressed:
            action = NextAction.NO_ACTION
        action_reasons = {
            NextAction.RESEARCH_MORE: (
                "Refresh incomplete or outdated research before initial outreach"
            ),
            NextAction.FIND_DECISION_MAKER: (
                "READY research needs a current supported individual contact"
            ),
            NextAction.SEND_LINKEDIN_CONNECTION: (
                "Published personal LinkedIn profile and no recorded outbound outreach"
            ),
            NextAction.SEND_LINKEDIN_MESSAGE: (
                "Recorded ACCEPTED invitation permits human message preparation"
            ),
            NextAction.SEND_EMAIL: "Published email channel and no recorded outbound outreach",
            NextAction.SEND_WHATSAPP: (
                "Explicit public business-facing WhatsApp channel; not inferred from a phone"
            ),
            NextAction.MAKE_PHONE_CALL: "Published phone channel and no recorded outbound outreach",
            NextAction.PREPARE_CONTACT_FORM: (
                "Published contact form and supported person; prepare company-inbox text for review"
            ),
            NextAction.FOLLOW_UP: (
                "Recorded outreach and expired cooldown; review follow-up manually"
            ),
            NextAction.PREPARE_MEETING: "Current MEETING stage requires internal preparation",
            NextAction.REVIEW_MANUALLY: (
                "Review contactability, recorded acceptance and pipeline history before acting"
            ),
            NextAction.NO_ACTION: "Suppression rules prohibit a recommendation to act now",
        }
        reasons.append(action_reasons.get(action, "Human preparation required"))
        reasons.extend(
            f"{component.dimension}: {component.points}/{component.maximum_points}"
            for component in components
        )
        refs = set(brief.supporting_evidence_ids if brief else ())
        if contact:
            refs.update(contact.evidence_ids)
        if channel:
            refs.add(channel.evidence_id)
        return ShortlistDecision(
            item=ShortlistItem(
                lead_id=c.lead.id,
                company_id=c.company.id,
                company_name=c.company.canonical_name,
                campaign_name=c.campaign.name,
                campaign_type=c.campaign.campaign_type,
                latest_score=total,
                score_id=score.id if score else None,
                score_band=band,
                score_completeness=score_completeness,
                brief_id=brief.id if brief else None,
                research_status=brief.status if brief else None,
                research_completeness=completeness,
                primary_opportunity=brief.opportunity_type
                if brief
                else OpportunityType.NO_CLEAR_OPPORTUNITY,
                opportunity_priority=brief.opportunity_priority
                if brief
                else OpportunityPriority.NONE,
                recommended_contact_id=contact.contact_id if contact else None,
                recommended_contact_name=contact.full_name if contact else None,
                recommended_contact_role=contact.current_role if contact else None,
                recommended_contact_fit=contact.fit_score if contact else None,
                recommended_channel=channel.channel_type if channel else None,
                recommended_channel_value=channel.value if channel else None,
                channel_evidence_id=channel.evidence_id if channel else None,
                target_roles=c.target_roles if not contact else (),
                suggested_contact_angle=brief.suggested_contact_angle
                if brief
                else "Clarify business goals and missing evidence before proposing outreach.",
                pipeline_status=c.lead.status,
                last_interaction_at=last.occurred_at if last else None,
                last_interaction_id=last.id if last else None,
                eligible_again_at=max(
                    [date for date in [eligible] if date and date > now]
                    + [record.expires_at for record in active],
                    default=None,
                ),
                recommended_next_action=action,
                shortlist_priority_score=sum(p.points for p in components),
                components=components,
                reasons=tuple(reasons),
                warnings=tuple(dict.fromkeys(warnings)),
                evidence_ids=tuple(sorted(refs)),
            ),
            suppression_reasons=tuple(suppressed),
        )

    def _contact(
        self, c: ShortlistContext, s: ShortlistSettings
    ) -> tuple[ContactRecommendation | None, PublicChannel | None]:
        evidence = {e.id: e for e in c.evidence}

        def valid_role(contact: ContactRecommendation) -> bool:
            return any(
                e.confidence >= 0.7
                and e.company_id == c.company.id
                and c.as_of - timedelta(days=s.contact_freshness_days) <= e.observed_at <= c.as_of
                and e.created_at <= c.as_of
                and isinstance(e.raw_value, dict)
                and e.raw_value.get("contact_id") == str(contact.contact_id)
                for eid in contact.evidence_ids
                if (e := evidence.get(eid)) is not None and e.evidence_type == "CONTACT_ROLE"
            )

        candidates = [
            contact
            for contact in c.contacts
            if contact.verification in {VerificationState.SUPPORTED, VerificationState.VERIFIED}
            and valid_role(contact)
        ]
        candidates.sort(
            key=lambda contact: (
                -contact.fit_score,
                normalize_name(contact.full_name),
                str(contact.contact_id),
            )
        )
        for contact in candidates:
            channels = []
            public_channels = list(contact.channels)
            for evidence_item in c.evidence:
                extra_type = {
                    "CONTACT_PUBLIC_WHATSAPP": "WHATSAPP",
                    "CONTACT_PUBLIC_CONTACT_FORM": "WEBSITE_CONTACT_FORM",
                }.get(evidence_item.evidence_type)
                if (
                    extra_type
                    and isinstance(evidence_item.raw_value, dict)
                    and evidence_item.raw_value.get("contact_id") == str(contact.contact_id)
                ):
                    public_channels.append(
                        PublicChannel(
                            channel_type=extra_type,
                            value=evidence_item.statement,
                            evidence_id=evidence_item.id,
                        )
                    )
            for channel in public_channels:
                e = evidence.get(channel.evidence_id)
                if (
                    channel.channel_type in s.channel_preference
                    and e
                    and e.company_id == c.company.id
                    and e.confidence >= 0.7
                    and c.as_of - timedelta(days=s.contact_freshness_days)
                    <= e.observed_at
                    <= c.as_of
                    and e.created_at <= c.as_of
                    and isinstance(e.raw_value, dict)
                    and e.raw_value.get("contact_id") == str(contact.contact_id)
                    and e.statement == channel.value
                    and e.evidence_type
                    == {
                        "LINKEDIN": "CONTACT_PUBLIC_PROFILE",
                        "EMAIL": "CONTACT_PUBLIC_EMAIL",
                        "PHONE": "CONTACT_PUBLIC_PHONE",
                        "WEBSITE_CONTACT_FORM": "CONTACT_PUBLIC_CONTACT_FORM",
                        "WHATSAPP": "CONTACT_PUBLIC_WHATSAPP",
                    }.get(channel.channel_type)
                    and (
                        channel.channel_type != "WHATSAPP"
                        or e.raw_value.get("business_facing") is True
                    )
                ):
                    channels.append(channel)
            channels.sort(
                key=lambda channel: (
                    s.channel_preference.index(channel.channel_type),
                    channel.value,
                )
            )
            if channels:
                return contact, channels[0]
        return (candidates[0], None) if candidates else (None, None)

    def _action(
        self,
        c: ShortlistContext,
        usable: bool,
        contact: ContactRecommendation | None,
        channel: PublicChannel | None,
        outbound: list[LeadInteraction],
        future: bool,
    ) -> NextAction:
        if c.lead.status == LeadStatus.MEETING:
            return NextAction.PREPARE_MEETING
        if c.lead.status == LeadStatus.RESPONDED:
            return NextAction.REVIEW_MANUALLY
        if future:
            return NextAction.REVIEW_MANUALLY
        if outbound and outbound[-1].lead_id != c.lead.id:
            return NextAction.REVIEW_MANUALLY
        if c.lead.status == LeadStatus.PROPOSAL:
            return (
                NextAction.FOLLOW_UP
                if any(i.interaction_type == InteractionType.PROPOSAL_SENT for i in outbound)
                else NextAction.REVIEW_MANUALLY
            )
        if not usable or not c.brief or c.brief.status != BriefStatus.READY:
            return NextAction.RESEARCH_MORE
        if not contact:
            return NextAction.FIND_DECISION_MAKER
        if not channel:
            return NextAction.REVIEW_MANUALLY
        if outbound:
            latest = outbound[-1]
            if latest.interaction_type == InteractionType.LINKEDIN_CONNECTION:
                return (
                    NextAction.SEND_LINKEDIN_MESSAGE
                    if (latest.outcome or "").strip().upper() == "ACCEPTED"
                    and channel.channel_type == "LINKEDIN"
                    else NextAction.REVIEW_MANUALLY
                )
            return NextAction.FOLLOW_UP
        if c.lead.status == LeadStatus.CONTACTED:
            return NextAction.REVIEW_MANUALLY
        return {
            "LINKEDIN": NextAction.SEND_LINKEDIN_CONNECTION,
            "EMAIL": NextAction.SEND_EMAIL,
            "PHONE": NextAction.MAKE_PHONE_CALL,
            "WHATSAPP": NextAction.SEND_WHATSAPP,
            "WEBSITE_CONTACT_FORM": NextAction.PREPARE_CONTACT_FORM,
        }.get(channel.channel_type, NextAction.REVIEW_MANUALLY)
