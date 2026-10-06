"""Prepare local drafts and record explicit human decisions; no sending port exists."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import ValidationError

from lead_engine.application.discovery import UnitOfWorkFactory
from lead_engine.application.ports import Repository
from lead_engine.application.shortlist import DailyShortlistService
from lead_engine.domain.contact_research import ContactRecommendation
from lead_engine.domain.enums import InteractionType, SourceType
from lead_engine.domain.errors import DuplicateError, NotFoundError
from lead_engine.domain.models import Contact, Evidence, Lead, LeadInteraction, Source, utc_now
from lead_engine.domain.outreach import (
    DraftStatus,
    DraftView,
    GroundedClaim,
    OutreachChannel,
    OutreachDraft,
    OutreachEvent,
    OutreachLanguage,
    OutreachPolicy,
    OutreachPurpose,
    OutreachSettings,
    ReferralObservation,
)
from lead_engine.domain.outreach_policy import OBSERVATIONS, V1OutreachPolicy
from lead_engine.domain.policies import Observations
from lead_engine.domain.research import BriefStatus, EvidenceCitation
from lead_engine.domain.scoring import ScoringContext, SignalState
from lead_engine.domain.shortlist import NextAction, ShortlistFilters, ShortlistSettings

ACTIONS = {
    NextAction.SEND_LINKEDIN_CONNECTION,
    NextAction.SEND_LINKEDIN_MESSAGE,
    NextAction.SEND_EMAIL,
    NextAction.SEND_WHATSAPP,
    NextAction.MAKE_PHONE_CALL,
    NextAction.PREPARE_CONTACT_FORM,
    NextAction.FOLLOW_UP,
}
CHANNEL_INTERACTIONS = {
    OutreachChannel.LINKEDIN: InteractionType.LINKEDIN_MESSAGE,
    OutreachChannel.EMAIL: InteractionType.EMAIL,
    OutreachChannel.WHATSAPP: InteractionType.WHATSAPP,
    OutreachChannel.PHONE_SCRIPT: InteractionType.PHONE_CALL,
}


class OutreachDraftService:
    def __init__(
        self,
        factory: UnitOfWorkFactory,
        settings: OutreachSettings | None = None,
        shortlist_settings: ShortlistSettings | None = None,
        policy: OutreachPolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.factory = factory
        self.settings = settings or OutreachSettings()
        self.shortlist_settings = shortlist_settings or ShortlistSettings()
        self.policy = policy or V1OutreachPolicy()
        self.clock = clock

    def _now(self) -> datetime:
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("Outreach requires an aware clock")
        return now

    def _view(self, repo: Repository, draft: OutreachDraft) -> DraftView:
        events = tuple(repo.list(OutreachEvent, draft_id=draft.id))
        return DraftView(
            draft=draft,
            status=events[-1].status if events else DraftStatus.DRAFT,
            approved_at=next(
                (e.occurred_at for e in events if e.status == DraftStatus.APPROVED), None
            ),
            rejected_at=next(
                (e.occurred_at for e in events if e.status == DraftStatus.REJECTED), None
            ),
            used_at=next((e.occurred_at for e in events if e.status == DraftStatus.USED), None),
            events=events,
        )

    def show(self, draft_id: UUID) -> DraftView:
        with self.factory() as uow:
            draft = uow.repository.get(OutreachDraft, draft_id)
            if draft is None:
                raise NotFoundError("Draft not found")
            return self._view(uow.repository, draft)

    def list_drafts(
        self, status: DraftStatus | None = None, lead_id: UUID | None = None
    ) -> list[DraftView]:
        with self.factory() as uow:
            records = uow.repository.list(
                OutreachDraft, **({"lead_id": lead_id} if lead_id else {})
            )
            views = [self._view(uow.repository, d) for d in records]
            return sorted(
                (v for v in views if status is None or v.status == status),
                key=lambda v: (v.draft.created_at, str(v.draft.id)),
                reverse=True,
            )

    def _refs(
        self, repo: Repository, ids: set[UUID], company_id: UUID, now: datetime
    ) -> tuple[EvidenceCitation, ...]:
        citations = []
        for eid in sorted(ids):
            evidence = repo.get(Evidence, eid)
            if (
                evidence is None
                or evidence.company_id != company_id
                or evidence.created_at > now
                or evidence.observed_at > now
            ):
                raise ValueError("Missing, foreign or future evidence reference")
            source = repo.get(Source, evidence.source_id)
            if source is None or source.retrieved_at > now:
                raise ValueError("Evidence source unavailable or future-dated")
            citations.append(
                EvidenceCitation(
                    evidence_id=eid,
                    source_id=source.id,
                    source_url=source.url,
                    source_title=source.title,
                    evidence_type=evidence.evidence_type,
                    statement=evidence.statement,
                    observed_at=evidence.observed_at,
                )
            )
        return tuple(citations)

    def _referral(
        self, evidence: tuple[Evidence, ...], contact_id: UUID, now: datetime
    ) -> tuple[Evidence, ReferralObservation] | None:
        candidates = []
        for e in evidence:
            if e.evidence_type not in {"REFERRAL_PATH", "WARM_INTRODUCTION"} or e.confidence < 0.7:
                continue
            if (
                not now - timedelta(days=self.settings.referral_freshness_days)
                <= e.observed_at
                <= now
                or e.created_at > now
            ):
                continue
            try:
                referral = ReferralObservation.model_validate(e.raw_value)
            except ValidationError:
                continue
            if referral.contact_id == contact_id:
                candidates.append((e, referral))
        if not candidates:
            return None
        latest_time = max(e.observed_at for e, _ in candidates)
        latest = [(e, path) for e, path in candidates if e.observed_at == latest_time]
        if any(not path.recommended_contact for _, path in latest):
            return None
        if len({path.referrer_name for _, path in latest}) != 1:
            return None
        return max(latest, key=lambda pair: (pair[0].confidence, str(pair[0].id)))

    def draft(
        self,
        lead_id: UUID,
        channel: OutreachChannel | None = None,
        purpose: OutreachPurpose | None = None,
        language: OutreachLanguage = OutreachLanguage.ES,
        force: bool = False,
    ) -> DraftView:
        now = self._now()
        settings = self.shortlist_settings
        if channel:
            channel_type = "PHONE" if channel == OutreachChannel.PHONE_SCRIPT else channel.value
            settings = ShortlistSettings.model_validate(
                {**settings.model_dump(), "channel_preference": (channel_type,)}
            )
        ranking = DailyShortlistService(self.factory, settings, clock=lambda: now)
        with self.factory() as uow:
            repo = uow.repository
            lead = repo.get(Lead, lead_id)
            if lead is None:
                raise NotFoundError("Lead not found")
            context = ranking.build_context(repo, lead, now)
            decision = ranking.policy.evaluate(context, ShortlistFilters(), settings)
            item = decision.item
            action = item.recommended_next_action
            if decision.suppression_reasons or action not in ACTIONS:
                raise ValueError(f"Shortlist requires {action.value}; no outreach draft allowed")
            brief = context.brief
            if (
                brief is None
                or brief.primary_opportunity is None
                or brief.status not in {BriefStatus.READY, BriefStatus.PARTIAL}
            ):
                raise ValueError("Supported commercial opportunity required")
            if brief.status == BriefStatus.PARTIAL and brief.data_completeness < 70:
                raise ValueError("Partial brief insufficient for outreach")
            contact = next(
                (c for c in context.contacts if c.contact_id == item.recommended_contact_id), None
            )
            if (
                contact is None
                or item.channel_evidence_id is None
                or item.recommended_channel is None
                or item.recommended_channel_value is None
            ):
                raise ValueError("Supported person and public channel required")
            actual_channel = OutreachChannel(
                "PHONE_SCRIPT" if item.recommended_channel == "PHONE" else item.recommended_channel
            )
            referral = self._referral(context.evidence, contact.contact_id, now)
            connection = action == NextAction.SEND_LINKEDIN_CONNECTION
            if purpose is None:
                purpose = (
                    OutreachPurpose.FOLLOW_UP
                    if action == NextAction.FOLLOW_UP
                    else OutreachPurpose.POST_CONNECTION_MESSAGE
                    if action == NextAction.SEND_LINKEDIN_MESSAGE
                    else OutreachPurpose.REFERRAL_INTRO
                    if referral
                    else OutreachPurpose.LINKEDIN_CONNECTION
                    if connection
                    else OutreachPurpose.EMAIL_INTRO
                    if actual_channel == OutreachChannel.EMAIL
                    else OutreachPurpose.WHATSAPP_INTRO
                    if actual_channel == OutreachChannel.WHATSAPP
                    else OutreachPurpose.FIRST_CONTACT
                )
            self._purpose(purpose, action, actual_channel, referral is not None)
            previous = self._previous(repo, lead.id, contact, actual_channel, action, now)
            refs = (
                set(brief.primary_opportunity.supporting_evidence_ids)
                | set(contact.evidence_ids)
                | {item.channel_evidence_id}
            )
            claims = []
            observation = ""
            if not connection:
                observations = Observations(
                    ScoringContext(
                        lead=lead,
                        company=context.company,
                        campaign=context.campaign,
                        evidence=tuple(
                            e
                            for e in context.evidence
                            if e.created_at <= now
                            and now - timedelta(days=settings.brief_freshness_days)
                            <= e.observed_at
                            <= now
                        ),
                        contacts=(),
                        audits=context.audits,
                        as_of=now,
                    )
                )
                for signal in sorted(OBSERVATIONS):
                    fact = observations.fact(signal)
                    if (
                        fact.state == SignalState.PRESENT
                        and fact.value is True
                        and fact.evidence_ids
                        and set(fact.evidence_ids) <= set(brief.supporting_evidence_ids)
                    ):
                        observation = OBSERVATIONS[signal][int(language == OutreachLanguage.EN)]
                        claims.append(
                            GroundedClaim(text=observation, evidence_ids=fact.evidence_ids)
                        )
                        refs.update(fact.evidence_ids)
                        break
            referral_text = ""
            if referral and action != NextAction.FOLLOW_UP:
                e, path = referral
                referral_text = (
                    f"{path.referrer_name} recommended that I contact you."
                    if language == OutreachLanguage.EN
                    else f"{path.referrer_name} me recomendó escribirte."
                )
                claims.append(GroundedClaim(text=referral_text, evidence_ids=(e.id,)))
                refs.add(e.id)
            refs.update(
                e.id
                for e in context.evidence
                if self._authority((e,), contact.contact_id, now, settings.contact_freshness_days)
            )
            authority = (
                self._authority(
                    context.evidence, contact.contact_id, now, settings.contact_freshness_days
                )
                and contact.fit_score >= 70
            )
            subject, body = self.policy.render(
                campaign=context.campaign.campaign_type.value,
                opportunity=brief.opportunity_type.value,
                role=contact.role_category,
                language=language,
                channel=actual_channel,
                purpose=purpose,
                connection=connection,
                company=context.company.canonical_name,
                name=contact.full_name,
                observation=observation,
                referral=referral_text,
                authority=authority,
            )
            # A custom policy cannot introduce arbitrary prose; compare with the closed V1 renderer.
            expected = V1OutreachPolicy().render(
                campaign=context.campaign.campaign_type.value,
                opportunity=brief.opportunity_type.value,
                role=contact.role_category,
                language=language,
                channel=actual_channel,
                purpose=purpose,
                connection=connection,
                company=context.company.canonical_name,
                name=contact.full_name,
                observation=observation,
                referral=referral_text,
                authority=authority,
            )
            if self.policy.version != "outreach-v1" or (subject, body) != expected:
                raise ValueError("Unsupported claim or unrecognized outreach policy")
            warnings = [
                "Human approval required. Approval never sends; use is a human attestation."
            ]
            if not observation:
                warnings.append(
                    "No personalized business observation used; "
                    "opportunity angle is a hypothesis from the brief."
                )
            if not authority:
                warnings.append("Decision authority is unconfirmed; ask who handles this area.")
            draft = OutreachDraft(
                lead_id=lead.id,
                contact_id=contact.contact_id,
                company_name=context.company.canonical_name,
                contact_name=contact.full_name,
                contact_role=contact.current_role,
                role_category=contact.role_category,
                created_at=now,
                outreach_version=self.policy.version,
                channel=actual_channel,
                channel_value=item.recommended_channel_value,
                purpose=purpose,
                language=language,
                subject=subject,
                body=body,
                connection=connection,
                claims=tuple(claims),
                supporting_evidence_ids=tuple(sorted(refs)),
                evidence_citations=self._refs(repo, refs, context.company.id, now),
                commercial_brief_id=brief.id,
                shortlist_action=action.value,
                previous_interaction_id=previous.id if previous else None,
                referral_evidence_id=referral[0].id if referral_text and referral else None,
                warnings=tuple(warnings),
                settings=self.settings,
            )
            peers = [
                d
                for d in repo.list(OutreachDraft, lead_id=lead.id)
                if d.contact_id == draft.contact_id
                and d.channel == draft.channel
                and (d.purpose == draft.purpose or d.body == draft.body)
                and d.language == draft.language
            ]
            if not force and any(
                now - timedelta(days=self.settings.duplicate_days) <= d.created_at <= now
                for d in peers
            ):
                raise DuplicateError("Recent draft already exists; review history or use --force")
            repo.add(draft)
            for old in peers:
                if self._view(repo, old).status == DraftStatus.DRAFT:
                    repo.add(
                        OutreachEvent(
                            draft_id=old.id,
                            occurred_at=now,
                            status=DraftStatus.SUPERSEDED,
                            reason=f"Replaced by draft {draft.id}",
                            actor="SYSTEM",
                        )
                    )
            uow.commit()
            return DraftView(draft=draft)

    @staticmethod
    def _authority(
        evidence: tuple[Evidence, ...], contact_id: UUID, now: datetime, days: int
    ) -> bool:
        candidates = [
            e
            for e in evidence
            if e.evidence_type == "DECISION_MAKER_ACCESS"
            and e.confidence >= 0.7
            and now - timedelta(days=days) <= e.observed_at <= now
            and e.created_at <= now
            and isinstance(e.raw_value, dict)
            and e.raw_value.get("contact_id") == str(contact_id)
        ]
        if not candidates:
            return False
        latest_time = max(e.observed_at for e in candidates)
        latest = [e for e in candidates if e.observed_at == latest_time]
        return all(
            isinstance(e.raw_value, dict) and e.raw_value.get("authority_confirmed") is True
            for e in latest
        )

    @staticmethod
    def _purpose(
        purpose: OutreachPurpose, action: NextAction, channel: OutreachChannel, referral: bool
    ) -> None:
        if (purpose == OutreachPurpose.FOLLOW_UP) != (action == NextAction.FOLLOW_UP):
            raise ValueError("Follow-up requires an eligible recorded previous interaction")
        if (
            purpose == OutreachPurpose.POST_CONNECTION_MESSAGE
            and action != NextAction.SEND_LINKEDIN_MESSAGE
        ):
            raise ValueError("Post-connection message requires recorded accepted invitation")
        if purpose == OutreachPurpose.REFERRAL_INTRO and not referral:
            raise ValueError("No explicit supported referral")
        required = {
            OutreachPurpose.LINKEDIN_CONNECTION: OutreachChannel.LINKEDIN,
            OutreachPurpose.LINKEDIN_MESSAGE: OutreachChannel.LINKEDIN,
            OutreachPurpose.POST_CONNECTION_MESSAGE: OutreachChannel.LINKEDIN,
            OutreachPurpose.EMAIL_INTRO: OutreachChannel.EMAIL,
            OutreachPurpose.WHATSAPP_INTRO: OutreachChannel.WHATSAPP,
        }
        if purpose in required and channel != required[purpose]:
            raise ValueError("Purpose does not match public channel")
        if (
            purpose == OutreachPurpose.LINKEDIN_CONNECTION
            and action != NextAction.SEND_LINKEDIN_CONNECTION
        ):
            raise ValueError("Connection is not currently recommended")
        if action == NextAction.SEND_LINKEDIN_CONNECTION and purpose in {
            OutreachPurpose.LINKEDIN_MESSAGE,
            OutreachPurpose.POST_CONNECTION_MESSAGE,
            OutreachPurpose.MEETING_REQUEST,
        }:
            raise ValueError("First LinkedIn action is an invitation, not an unrestricted message")

    @staticmethod
    def _previous(
        repo: Repository,
        lead_id: UUID,
        contact: ContactRecommendation,
        channel: OutreachChannel,
        action: NextAction,
        now: datetime,
    ) -> LeadInteraction | None:
        if action not in {NextAction.FOLLOW_UP, NextAction.SEND_LINKEDIN_MESSAGE}:
            return None
        expected = (
            InteractionType.LINKEDIN_CONNECTION
            if action == NextAction.SEND_LINKEDIN_MESSAGE
            else CHANNEL_INTERACTIONS.get(channel)
        )
        records = [
            i
            for i in repo.list(LeadInteraction, lead_id=lead_id)
            if i.contact_id == contact.contact_id
            and i.interaction_type == expected
            and i.occurred_at <= now
        ]
        if not records:
            raise ValueError(
                "No matching previous contact/channel interaction; manual handling required"
            )
        latest = max(records, key=lambda i: (i.occurred_at, str(i.id)))
        if (
            action == NextAction.SEND_LINKEDIN_MESSAGE
            and (latest.outcome or "").upper() != "ACCEPTED"
        ):
            raise ValueError("Invitation acceptance is not recorded for this contact")
        return latest

    def transition(
        self,
        draft_id: UUID,
        status: DraftStatus,
        reason: str | None = None,
        record_interaction: bool = False,
    ) -> DraftView:
        now = self._now()
        with self.factory() as uow:
            repo = uow.repository
            draft = repo.get(OutreachDraft, draft_id)
            if draft is None:
                raise NotFoundError("Draft not found")
            view = self._view(repo, draft)
            allowed = {
                DraftStatus.DRAFT: {DraftStatus.APPROVED, DraftStatus.REJECTED},
                DraftStatus.APPROVED: {DraftStatus.USED, DraftStatus.REJECTED},
            }
            if status not in allowed.get(view.status, set()):
                raise ValueError(f"Invalid human transition {view.status} -> {status}")
            if now < draft.created_at or any(e.occurred_at > now for e in view.events):
                raise ValueError("Decision timestamp precedes draft/history")
            if status == DraftStatus.REJECTED and not (reason or "").strip():
                raise ValueError("Rejection requires a reason")
            if record_interaction and status != DraftStatus.USED:
                raise ValueError("Only explicit mark-used can record an interaction")
            interaction_id = None
            if record_interaction:
                kind = (
                    InteractionType.LINKEDIN_CONNECTION
                    if draft.connection
                    else CHANNEL_INTERACTIONS.get(draft.channel)
                )
                if kind is None:
                    raise ValueError(
                        "Contact-form use has no matching interaction type; "
                        "record use without activity"
                    )
                activity = LeadInteraction(
                    lead_id=draft.lead_id,
                    contact_id=draft.contact_id,
                    interaction_type=kind,
                    occurred_at=now,
                    notes=(
                        f"Human attested external use of draft {draft.id}; "
                        "delivery/response not verified."
                    ),
                )
                repo.add(activity)
                interaction_id = activity.id
            repo.add(
                OutreachEvent(
                    draft_id=draft.id,
                    sequence=len(view.events) + 1,
                    occurred_at=now,
                    status=status,
                    reason=reason,
                    interaction_id=interaction_id,
                )
            )
            result = self._view(repo, draft)
            uow.commit()
            return result

    def record_referral(
        self,
        lead_id: UUID,
        contact_id: UUID,
        referrer_name: str,
        source_url: str,
        statement: str,
        warm_introduction: bool = False,
    ) -> Evidence:
        now = self._now()
        path = ReferralObservation(
            contact_id=contact_id, referrer_name=referrer_name, recommended_contact=True
        )
        with self.factory() as uow:
            repo = uow.repository
            lead = repo.get(Lead, lead_id)
            contact = repo.get(Contact, contact_id)
            if lead is None or contact is None:
                raise NotFoundError("Lead/contact not found")
            if lead.company_id != contact.company_id:
                raise ValueError("Referral contact belongs to another company")
            source = Source(
                source_type=SourceType.MANUAL,
                url=source_url,
                title="Human-attested referral",
                retrieved_at=now,
            )
            evidence = Evidence(
                company_id=lead.company_id,
                source_id=source.id,
                evidence_type="WARM_INTRODUCTION" if warm_introduction else "REFERRAL_PATH",
                statement=statement,
                raw_value=path.model_dump(mode="json"),
                observed_at=now,
                created_at=now,
                confidence=1,
            )
            repo.add(source)
            repo.add(evidence)
            uow.commit()
            return evidence

    def shortlist(
        self,
        filters: ShortlistFilters | None = None,
        language: OutreachLanguage = OutreachLanguage.ES,
    ) -> tuple[list[DraftView], list[str]]:
        ranking = DailyShortlistService(self.factory, self.shortlist_settings, clock=self.clock)
        requested = filters or ShortlistFilters()
        pool = ShortlistFilters.model_validate({**requested.model_dump(), "limit": 100})
        run = ranking.today(pool)
        actionable = [item for item in run.items if item.recommended_next_action in ACTIONS]
        warm = set()
        with self.factory() as uow:
            for item in actionable:
                if item.recommended_contact_id and self._referral(
                    tuple(uow.repository.list(Evidence, company_id=item.company_id)),
                    item.recommended_contact_id,
                    run.generated_at,
                ):
                    warm.add(item.lead_id)
        actionable.sort(key=lambda item: item.lead_id not in warm)
        views = []
        skipped = [
            f"{item.company_name}: {item.recommended_next_action}"
            for item in run.items
            if item.recommended_next_action not in ACTIONS
        ]
        for item in actionable[: requested.limit]:
            try:
                views.append(self.draft(item.lead_id, language=language))
            except (ValueError, DuplicateError) as error:
                skipped.append(f"{item.company_name}: {error}")
        # Prefer explicit warm paths for human review; never change cooldown eligibility.
        views.sort(key=lambda v: v.draft.referral_evidence_id is None)
        return views, skipped
