"""Generate and persist append-only briefs; no scoring, HTTP or pipeline mutations."""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from lead_engine.application.contact_research import DecisionMakerResearchService
from lead_engine.application.discovery import UnitOfWorkFactory
from lead_engine.domain.audit import WebsiteAudit
from lead_engine.domain.contact_research import RecommendationResult
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.errors import NotFoundError
from lead_engine.domain.models import (
    Campaign,
    Company,
    Contact,
    Evidence,
    Lead,
    LeadScore,
    Source,
    utc_now,
)
from lead_engine.domain.research import CommercialBrief, ResearchContext, ResearchPolicy
from lead_engine.domain.research_policies import DEFAULT_RESEARCH_POLICIES
from lead_engine.domain.scoring import ScoreSummary, ScoringContext, score_band


class DecisionMakerRecommendations(Protocol):
    def recommend_contacts(self, lead_id: UUID, limit: int = 3) -> RecommendationResult: ...


class CommercialResearchService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        recommendations: DecisionMakerRecommendations | None = None,
        policies: Mapping[CampaignType, ResearchPolicy] | None = None,
    ) -> None:
        self.factory = uow_factory
        self.recommendations = recommendations or DecisionMakerResearchService(uow_factory)
        self.policies = dict(policies or DEFAULT_RESEARCH_POLICIES)

    def research_lead(self, lead_id: UUID) -> CommercialBrief:
        contacts = self.recommendations.recommend_contacts(lead_id, limit=3)
        with self.factory() as uow:
            repo = uow.repository
            lead = repo.get(Lead, lead_id)
            if lead is None:
                raise NotFoundError("Lead not found")
            company = repo.get(Company, lead.company_id)
            campaign = repo.get(Campaign, lead.campaign_id)
            if company is None or campaign is None:
                raise NotFoundError("Lead relationships missing")
            policy = self.policies.get(campaign.campaign_type)
            if policy is None:
                raise ValueError("No research policy registered for campaign")
            cutoff = utc_now()
            evidence = tuple(
                e
                for e in repo.list(Evidence, company_id=company.id)
                if e.created_at <= cutoff and e.observed_at <= cutoff
            )
            scores = [s for s in repo.list(LeadScore, lead_id=lead.id) if s.calculated_at <= cutoff]
            latest = max(scores, key=lambda s: (s.calculated_at, str(s.id))) if scores else None
            band = None
            if latest:
                try:
                    summary = ScoreSummary.model_validate_json(latest.explanation or "")
                    band = score_band(latest.total_score, summary.bands)
                except ValueError:
                    band = score_band(latest.total_score, (85, 70, 55, 40))
            context = ResearchContext(
                observations=ScoringContext(
                    lead=lead,
                    company=company,
                    campaign=campaign,
                    evidence=evidence,
                    contacts=tuple(repo.list(Contact, company_id=company.id)),
                    audits=tuple(repo.list(WebsiteAudit, company_id=company.id)),
                    as_of=cutoff,
                ),
                sources=tuple(
                    source
                    for sid in sorted({e.source_id for e in evidence})
                    if (source := repo.get(Source, sid)) is not None
                ),
                latest_score=latest,
                score_band=band,
                recommendations=tuple(contacts.contacts),
                target_roles=tuple(contacts.target_roles),
            )
            brief = CommercialBrief.model_validate(policy.generate(context).model_dump())
            if (
                brief.lead_id != lead.id
                or brief.company_id != company.id
                or brief.campaign_id != campaign.id
                or brief.research_version != policy.version
                or brief.generated_at != cutoff
            ):
                raise ValueError("Research policy returned inconsistent identity/version/cutoff")
            evidence_map = {e.id: e for e in evidence}
            source_map = {s.id: s for s in context.sources}
            for citation in brief.evidence_citations:
                item = evidence_map.get(citation.evidence_id)
                if (
                    item is None
                    or item.source_id != citation.source_id
                    or citation.source_id not in source_map
                ):
                    raise ValueError("Brief references evidence outside the lead company snapshot")
            if brief.lead_score_id != (latest.id if latest else None):
                raise ValueError("Brief must reference the latest lead score")
            if brief.latest_lead_score != (float(latest.total_score) if latest else None):
                raise ValueError("Brief score differs from persisted score")
            for citation in brief.evidence_citations:
                item = evidence_map[citation.evidence_id]
                source = source_map[citation.source_id]
                if (
                    citation.statement != item.statement
                    or citation.observed_at != item.observed_at
                    or citation.evidence_type != item.evidence_type
                    or citation.source_url != source.url
                    or citation.source_title != source.title
                ):
                    raise ValueError("Brief citation differs from stored evidence/source")
            contact_ids = {c.id for c in context.observations.contacts}
            if any(c.contact_id not in contact_ids for c in brief.decision_maker_recommendations):
                raise ValueError("Brief contact belongs to another company")
            repo.add(brief)
            uow.commit()
            return brief

    def history(self, lead_id: UUID) -> list[CommercialBrief]:
        with self.factory() as uow:
            if uow.repository.get(Lead, lead_id) is None:
                raise NotFoundError("Lead not found")
            return sorted(
                uow.repository.list(CommercialBrief, lead_id=lead_id),
                key=lambda b: (b.generated_at, str(b.id)),
                reverse=True,
            )

    def latest(self, lead_id: UUID) -> CommercialBrief:
        history = self.history(lead_id)
        if not history:
            raise NotFoundError("No saved research brief; run research lead first")
        return history[0]

    def list_campaign(self, campaign_id: UUID) -> list[CommercialBrief]:
        with self.factory() as uow:
            if uow.repository.get(Campaign, campaign_id) is None:
                raise NotFoundError("Campaign not found")
            briefs = uow.repository.list(CommercialBrief, campaign_id=campaign_id)
        latest: dict[UUID, CommercialBrief] = {}
        for brief in sorted(briefs, key=lambda b: (b.generated_at, str(b.id)), reverse=True):
            latest.setdefault(brief.lead_id, brief)
        return sorted(latest.values(), key=lambda b: (b.company_name.casefold(), str(b.lead_id)))

    def research_campaign(
        self, campaign_id: UUID, min_score: int | None = None, limit: int = 5
    ) -> list[CommercialBrief]:
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be 1–100")
        if min_score is not None and not 0 <= min_score <= 100:
            raise ValueError("Score must be 0–100")
        with self.factory() as uow:
            if uow.repository.get(Campaign, campaign_id) is None:
                raise NotFoundError("Campaign not found")
            leads = uow.repository.list(Lead, campaign_id=campaign_id)
            eligible = []
            for lead in leads:
                scores = [
                    s
                    for s in uow.repository.list(LeadScore, lead_id=lead.id)
                    if s.calculated_at <= utc_now()
                ]
                latest = max(scores, key=lambda s: (s.calculated_at, str(s.id))) if scores else None
                if min_score is None or (latest and latest.total_score >= min_score):
                    eligible.append((lead, float(latest.total_score) if latest else -1))
        eligible.sort(key=lambda pair: (-pair[1], str(pair[0].id)))
        return [self.research_lead(lead.id) for lead, _ in eligible[:limit]]
