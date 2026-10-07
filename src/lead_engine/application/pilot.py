"""Append feedback about a frozen shortlist; summarize labels without changing the engine."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from lead_engine.application.discovery import UnitOfWorkFactory
from lead_engine.application.ports import Repository
from lead_engine.domain.errors import NotFoundError
from lead_engine.domain.models import Campaign, Lead, utc_now
from lead_engine.domain.outreach import OutreachDraft
from lead_engine.domain.pilot import (
    ContactDecision,
    DecisionMakerQuality,
    OutputQuality,
    PilotEvaluation,
    PilotReport,
    PilotReviewRow,
)
from lead_engine.domain.shortlist import DailyShortlistRun, NextAction


class PilotEvaluationService:
    def __init__(self, factory: UnitOfWorkFactory, clock: Callable[[], datetime] = utc_now) -> None:
        self.factory = factory
        self.clock = clock

    def _now(self) -> datetime:
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("Pilot clock requires an aware timestamp")
        return now

    def _run(
        self, repo: Repository, campaign_id: UUID, run_id: UUID | None, now: datetime
    ) -> DailyShortlistRun:
        if run_id:
            run = repo.get(DailyShortlistRun, run_id)
            if run is None:
                raise NotFoundError("Shortlist run not found")
            runs = [run]
        else:
            runs = repo.list(DailyShortlistRun)
        lead_ids = {lead.id for lead in repo.list(Lead, campaign_id=campaign_id)}
        matching = [
            run
            for run in runs
            if run.generated_at <= now
            and (
                run.filters.campaign_id == campaign_id
                or any(item.lead_id in lead_ids for item in run.items)
            )
        ]
        if not matching:
            raise ValueError(
                "No matching non-future shortlist run; generate a campaign shortlist first"
            )
        return max(matching, key=lambda r: (r.generated_at, str(r.id)))

    def evaluate(
        self,
        lead_id: UUID,
        would_contact: ContactDecision,
        decision_maker_quality: DecisionMakerQuality,
        opportunity_quality: OutputQuality,
        outreach_quality: OutputQuality,
        notes: str | None = None,
        evaluator: str = "Frank",
        shortlist_run_id: UUID | None = None,
        outreach_draft_id: UUID | None = None,
    ) -> PilotEvaluation:
        now = self._now()
        with self.factory() as uow:
            repo = uow.repository
            lead = repo.get(Lead, lead_id)
            if lead is None:
                raise NotFoundError("Lead not found")
            run = self._run(repo, lead.campaign_id, shortlist_run_id, now)
            item = next((i for i in run.items if i.lead_id == lead.id), None)
            if item is None:
                raise ValueError(
                    "Lead was not selected in this shortlist; "
                    "suppressed/unselected leads cannot be labeled as shortlisted"
                )
            if item.company_id != lead.company_id:
                raise ValueError("Shortlist company does not match lead")
            drafts = [
                d
                for d in repo.list(OutreachDraft, lead_id=lead.id)
                if d.commercial_brief_id == item.brief_id
                and d.contact_id == item.recommended_contact_id
                and d.created_at <= now
            ]
            if outreach_draft_id:
                draft = next((d for d in drafts if d.id == outreach_draft_id), None)
                if draft is None:
                    raise ValueError(
                        "Draft is missing, future or does not match reviewed lead/brief/contact"
                    )
            else:
                draft = max(drafts, key=lambda d: (d.created_at, str(d.id))) if drafts else None
            # Normalize through the domain model before choosing the per-evaluator revision.
            record = PilotEvaluation(
                lead_id=lead.id,
                company_id=lead.company_id,
                campaign_id=lead.campaign_id,
                shortlist_run_id=run.id,
                evaluator=evaluator,
                evaluated_at=now,
                revision=1,
                would_contact=would_contact,
                decision_maker_quality=decision_maker_quality,
                opportunity_quality=opportunity_quality,
                outreach_quality=outreach_quality,
                evaluator_notes=notes,
                score_id=item.score_id,
                commercial_brief_id=item.brief_id,
                recommended_contact_id=item.recommended_contact_id,
                outreach_draft_id=draft.id if draft else None,
            )
            history = repo.list(
                PilotEvaluation,
                lead_id=lead.id,
                shortlist_run_id=run.id,
                evaluator=record.evaluator,
            )
            if any(e.evaluated_at > now for e in history):
                raise ValueError("Evaluation timestamp precedes existing history")
            record = PilotEvaluation.model_validate(
                {
                    **record.model_dump(),
                    "revision": max((e.revision for e in history), default=0) + 1,
                }
            )
            repo.add(record)
            uow.commit()
            return record

    def history(self, lead_id: UUID) -> list[PilotEvaluation]:
        with self.factory() as uow:
            if uow.repository.get(Lead, lead_id) is None:
                raise NotFoundError("Lead not found")
            return sorted(
                uow.repository.list(PilotEvaluation, lead_id=lead_id),
                key=lambda e: (e.evaluated_at, e.revision, str(e.id)),
                reverse=True,
            )

    def report(
        self, campaign_id: UUID, evaluator: str = "Frank", shortlist_run_id: UUID | None = None
    ) -> PilotReport:
        now = self._now()
        with self.factory() as uow:
            repo = uow.repository
            campaign = repo.get(Campaign, campaign_id)
            if campaign is None:
                raise NotFoundError("Campaign not found")
            run = self._run(repo, campaign.id, shortlist_run_id, now)
            lead_ids = {lead.id for lead in repo.list(Lead, campaign_id=campaign.id)}
            cohort = [item for item in run.items if item.lead_id in lead_ids]
            evaluations = repo.list(
                PilotEvaluation,
                campaign_id=campaign.id,
                shortlist_run_id=run.id,
                evaluator=evaluator.strip(),
            )
            latest: dict[UUID, PilotEvaluation] = {}
            for e in evaluations:
                if e.evaluated_at <= now and (
                    e.lead_id not in latest or e.revision > latest[e.lead_id].revision
                ):
                    latest[e.lead_id] = e
            rows = []
            for item in cohort:
                evaluation = latest.get(item.lead_id)
                research = item.recommended_next_action in {
                    NextAction.RESEARCH_MORE,
                    NextAction.FIND_DECISION_MAKER,
                    NextAction.REVIEW_MANUALLY,
                }
                if evaluation:
                    research |= (
                        evaluation.would_contact == ContactDecision.MAYBE
                        or evaluation.decision_maker_quality != DecisionMakerQuality.GOOD
                        or evaluation.opportunity_quality != OutputQuality.GOOD
                    )
                rows.append(
                    PilotReviewRow(item=item, evaluation=evaluation, needs_more_research=research)
                )
            labels = [row.evaluation for row in rows if row.evaluation is not None]
            contact = {
                label: sum(e.would_contact == label for e in labels) for label in ContactDecision
            }
            decided = contact[ContactDecision.YES] + contact[ContactDecision.NO]
            return PilotReport(
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                evaluator=evaluator.strip(),
                generated_at=now,
                shortlist_run_id=run.id,
                shortlist_generated_at=run.generated_at,
                shortlisted_companies=len({row.item.company_id for row in rows}),
                companies_evaluated=len({e.company_id for e in labels}),
                unevaluated_lead_ids=tuple(
                    row.item.lead_id for row in rows if row.evaluation is None
                ),
                would_contact_distribution=contact,
                precision_denominator=decided,
                shortlist_precision_percent=round(100 * contact[ContactDecision.YES] / decided, 2)
                if decided
                else None,
                would_contact_percent=round(100 * contact[ContactDecision.YES] / len(labels), 2)
                if labels
                else None,
                decision_maker_quality_distribution={
                    label: sum(e.decision_maker_quality == label for e in labels)
                    for label in DecisionMakerQuality
                },
                opportunity_quality_distribution={
                    label: sum(e.opportunity_quality == label for e in labels)
                    for label in OutputQuality
                },
                outreach_quality_distribution={
                    label: sum(e.outreach_quality == label for e in labels)
                    for label in OutputQuality
                },
                false_positive_lead_ids=tuple(
                    e.lead_id for e in labels if e.would_contact == ContactDecision.NO
                ),
                leads_needing_more_research=tuple(
                    row.item.lead_id for row in rows if row.needs_more_research
                ),
                rows=tuple(rows),
            )
