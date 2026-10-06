"""Transactional scoring and historical reads; never mutate lead status/priority."""

from collections.abc import Mapping
from uuid import UUID

from lead_engine.application.discovery import UnitOfWorkFactory
from lead_engine.application.services import LeadService
from lead_engine.domain.audit import WebsiteAudit
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.errors import NotFoundError
from lead_engine.domain.models import Campaign, Company, Contact, Evidence, Lead, LeadScore, utc_now
from lead_engine.domain.policies import DEFAULT_POLICIES
from lead_engine.domain.scoring import (
    CampaignScoringPolicy,
    ScoreSummary,
    ScoringContext,
    ScoringResult,
    score_band,
)


class CommercialScoringService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        policies: Mapping[CampaignType, CampaignScoringPolicy] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._policies: Mapping[CampaignType, CampaignScoringPolicy] = dict(
            policies or DEFAULT_POLICIES
        )

    def score_lead(self, lead_id: UUID) -> ScoringResult:
        with self._uow_factory() as uow:
            repository = uow.repository
            lead = repository.get(Lead, lead_id)
            if lead is None:
                raise NotFoundError("Lead does not exist")
            company = repository.get(Company, lead.company_id)
            campaign = repository.get(Campaign, lead.campaign_id)
            if company is None or campaign is None:
                raise NotFoundError("Lead company/campaign does not exist")
            policy = self._policies.get(campaign.campaign_type)
            if policy is None:
                raise ValueError("No scoring policy registered for this campaign")
            context = ScoringContext(
                lead=lead,
                company=company,
                campaign=campaign,
                evidence=tuple(repository.list(Evidence, company_id=company.id)),
                contacts=tuple(repository.list(Contact, company_id=company.id)),
                audits=tuple(repository.list(WebsiteAudit, company_id=company.id)),
                as_of=utc_now(),
            )
            score, summary = policy.calculate(context)
            if score.lead_id != lead.id or score.scoring_version != policy.version:
                raise ValueError("Policy returned an inconsistent lead/version")
            if score_band(score.total_score, summary.bands) != summary.band:
                raise ValueError("Policy returned an inconsistent band")
            score = LeadScore.model_validate(
                {**score.model_dump(), "explanation": summary.model_dump_json()}
            )
            LeadService(uow).record_lead_score(score)
            uow.commit()
            return ScoringResult(score, summary, company.canonical_name, campaign.name)

    def history(self, lead_id: UUID) -> list[ScoringResult]:
        with self._uow_factory() as uow:
            lead = uow.repository.get(Lead, lead_id)
            if lead is None:
                raise NotFoundError("Lead does not exist")
            company = uow.repository.get(Company, lead.company_id)
            campaign = uow.repository.get(Campaign, lead.campaign_id)
            assert company is not None and campaign is not None
            scores = sorted(
                uow.repository.list(LeadScore, lead_id=lead_id),
                key=lambda score: (score.calculated_at, str(score.id)),
            )
            results = []
            for score in scores:
                try:
                    summary = ScoreSummary.model_validate_json(score.explanation or "")
                except ValueError:
                    summary = ScoreSummary(
                        band=score_band(score.total_score, (85, 70, 55, 40)),
                        completeness_percent=None,
                        known_weight=None,
                        input_cutoff=score.calculated_at.isoformat(),
                    )
                results.append(ScoringResult(score, summary, company.canonical_name, campaign.name))
            return results

    def campaign_leads(self, campaign_id: UUID) -> list[Lead]:
        with self._uow_factory() as uow:
            if uow.repository.get(Campaign, campaign_id) is None:
                raise NotFoundError("Campaign does not exist")
            return uow.repository.list(Lead, campaign_id=campaign_id)

    def score_campaign(self, campaign_id: UUID) -> list[ScoringResult]:
        return [self.score_lead(lead.id) for lead in self.campaign_leads(campaign_id)]

    def latest_campaign(self, campaign_id: UUID) -> list[ScoringResult]:
        results = []
        for lead in self.campaign_leads(campaign_id):
            history = self.history(lead.id)
            if history:
                results.append(history[-1])
        return sorted(
            results, key=lambda result: (-result.score.total_score, str(result.score.lead_id))
        )
