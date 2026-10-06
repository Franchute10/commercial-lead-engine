"""Read current records, evaluate policies and persist audit snapshots atomically."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from lead_engine.application.contact_research import DecisionMakerResearchService
from lead_engine.application.discovery import UnitOfWorkFactory
from lead_engine.application.ports import Repository
from lead_engine.domain.audit import WebsiteAudit
from lead_engine.domain.errors import NotFoundError
from lead_engine.domain.identity import normalize_name
from lead_engine.domain.models import (
    Campaign,
    Company,
    Evidence,
    Lead,
    LeadInteraction,
    LeadScore,
    utc_now,
)
from lead_engine.domain.research import CommercialBrief
from lead_engine.domain.shortlist import (
    DailyShortlistRun,
    NextAction,
    ShortlistContext,
    ShortlistDecision,
    ShortlistFilters,
    ShortlistItem,
    ShortlistPolicy,
    ShortlistSettings,
    ShortlistSuppression,
    SuppressionReason,
)
from lead_engine.domain.shortlist_policy import V1ShortlistPolicy


class DailyShortlistService:
    def __init__(
        self,
        factory: UnitOfWorkFactory,
        settings: ShortlistSettings | None = None,
        policy: ShortlistPolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.factory = factory
        self.settings = settings or ShortlistSettings()
        self.policy = policy or V1ShortlistPolicy()
        self.clock = clock

    def _now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise ValueError("Shortlist clock requires an aware timestamp")
        return value

    def build_context(self, repo: Repository, lead: Lead, as_of: datetime) -> ShortlistContext:
        company = repo.get(Company, lead.company_id)
        campaign = repo.get(Campaign, lead.campaign_id)
        if company is None or campaign is None:
            raise NotFoundError("Lead company/campaign missing")
        scores = [s for s in repo.list(LeadScore, lead_id=lead.id) if s.calculated_at <= as_of]
        briefs = [b for b in repo.list(CommercialBrief, lead_id=lead.id) if b.generated_at <= as_of]
        company_leads = repo.list(Lead, company_id=company.id)
        company_interactions = tuple(
            interaction
            for company_lead in company_leads
            for interaction in repo.list(LeadInteraction, lead_id=company_lead.id)
        )
        contacts = DecisionMakerResearchService(
            self.factory, self.settings.contact_freshness_days
        ).recommend_contacts(lead.id, as_of=as_of)
        return ShortlistContext(
            lead=lead,
            company=company,
            campaign=campaign,
            score=max(scores, key=lambda s: (s.calculated_at, str(s.id))) if scores else None,
            brief=max(briefs, key=lambda b: (b.generated_at, str(b.id))) if briefs else None,
            contacts=tuple(contacts.contacts),
            target_roles=tuple(contacts.target_roles),
            interactions=company_interactions,
            audits=tuple(repo.list(WebsiteAudit, company_id=company.id)),
            evidence=tuple(repo.list(Evidence, company_id=company.id)),
            suppressions=tuple(repo.list(ShortlistSuppression, lead_id=lead.id)),
            as_of=as_of,
        )

    def today(self, filters: ShortlistFilters | None = None) -> DailyShortlistRun:
        filters = filters or ShortlistFilters()
        as_of = self._now()
        decisions = []
        with self.factory() as uow:
            repo = uow.repository
            if filters.campaign_id is not None and repo.get(Campaign, filters.campaign_id) is None:
                raise NotFoundError("Campaign not found")
            for lead in repo.list(Lead):
                if filters.campaign_id and lead.campaign_id != filters.campaign_id:
                    continue
                company = repo.get(Company, lead.company_id)
                campaign = repo.get(Campaign, lead.campaign_id)
                if company is None or campaign is None:
                    raise NotFoundError("Lead relationships missing")
                if filters.campaign_type and campaign.campaign_type != filters.campaign_type:
                    continue
                if filters.city and normalize_name(company.city or "") != normalize_name(
                    filters.city
                ):
                    continue
                context = self.build_context(repo, lead, as_of)
                decision = self.policy.evaluate(context, filters, self.settings)
                if decision.item.lead_id != lead.id:
                    raise ValueError("Policy returned another lead")
                decisions.append(decision)
            eligible = [d.item for d in decisions if not d.suppression_reasons]
            eligible.sort(
                key=lambda item: (
                    -item.shortlist_priority_score,
                    -(item.latest_score or 0),
                    -item.research_completeness,
                    normalize_name(item.company_name),
                    str(item.lead_id),
                )
            )
            unique = []
            seen = set()
            for item in eligible:
                if item.company_id in seen:
                    duplicate = ShortlistItem.model_validate(
                        {
                            **item.model_dump(),
                            "recommended_next_action": NextAction.NO_ACTION,
                            "reasons": item.reasons
                            + (
                                "A higher-ranked lead at this exact company "
                                "already represents it in this run",
                            ),
                        }
                    )
                    decisions = [d for d in decisions if d.item.lead_id != item.lead_id]
                    decisions.append(
                        ShortlistDecision(
                            item=duplicate,
                            suppression_reasons=(SuppressionReason.DUPLICATE_COMPANY,),
                        )
                    )
                else:
                    seen.add(item.company_id)
                    unique.append(item)
            eligible = unique
            items = tuple(
                ShortlistItem.model_validate({**item.model_dump(), "shortlist_rank": index})
                for index, item in enumerate(eligible[: filters.limit], 1)
            )
            suppressed = tuple(
                sorted(
                    (d for d in decisions if d.suppression_reasons),
                    key=lambda d: str(d.item.lead_id),
                )
            )
            run = DailyShortlistRun(
                generated_at=as_of,
                policy_version=self.policy.version,
                filters=filters,
                settings=self.settings,
                candidates_considered=len(decisions),
                candidates_suppressed=len(suppressed),
                items_returned=len(items),
                items=items,
                suppressed=suppressed,
                eligible_not_selected=tuple(item.lead_id for item in eligible[filters.limit :]),
            )
            repo.add(run)
            uow.commit()
            return run

    def explain(self, lead_id: UUID) -> ShortlistDecision:
        as_of = self._now()
        with self.factory() as uow:
            lead = uow.repository.get(Lead, lead_id)
            if lead is None:
                raise NotFoundError("Lead not found")
            return self.policy.evaluate(
                self.build_context(uow.repository, lead, as_of), ShortlistFilters(), self.settings
            )

    def history(self, limit: int = 10) -> list[DailyShortlistRun]:
        if not 1 <= limit <= 100:
            raise ValueError("History limit must be 1–100")
        with self.factory() as uow:
            return sorted(
                uow.repository.list(DailyShortlistRun),
                key=lambda r: (r.generated_at, str(r.id)),
                reverse=True,
            )[:limit]

    def suppress(self, lead_id: UUID, days: int, reason: str) -> ShortlistSuppression:
        if not 1 <= days <= 365:
            raise ValueError("Suppression days must be 1–365")
        now = self._now()
        with self.factory() as uow:
            if uow.repository.get(Lead, lead_id) is None:
                raise NotFoundError("Lead not found")
            record = ShortlistSuppression(
                lead_id=lead_id, starts_at=now, expires_at=now + timedelta(days=days), reason=reason
            )
            uow.repository.add(record)
            uow.commit()
            return record

    def unsuppress(self, lead_id: UUID) -> int:
        now = self._now()
        with self.factory() as uow:
            if uow.repository.get(Lead, lead_id) is None:
                raise NotFoundError("Lead not found")
            active = [
                r
                for r in uow.repository.list(ShortlistSuppression, lead_id=lead_id)
                if r.active_at(now)
            ]
            for record in active:
                uow.repository.revoke_shortlist_suppression(record.id, now)
            uow.commit()
            return len(active)
