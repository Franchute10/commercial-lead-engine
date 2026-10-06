"""Six offline scenarios: actionable, role fallback, cooldown, partial, lost, suppressed."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from examples.scoring_fixture_demo import FixtureFetcher, create_fixtures
from lead_engine.application.auditor import WebsiteAuditService
from lead_engine.application.contact_research import DecisionMakerResearchService
from lead_engine.application.research import CommercialResearchService
from lead_engine.application.services import LeadService
from lead_engine.application.shortlist import DailyShortlistService
from lead_engine.application.shortlist_export import render_run
from lead_engine.domain.commercial import BusinessSignal
from lead_engine.domain.contact_research import ContactCandidate
from lead_engine.domain.enums import (
    CampaignType,
    InteractionType,
    LeadStatus,
    RoleCategory,
    SourceType,
)
from lead_engine.domain.models import (
    Campaign,
    Company,
    Evidence,
    Lead,
    LeadInteraction,
    LeadScore,
    ScoreComponent,
    Source,
    utc_now,
)
from lead_engine.domain.scoring import ScoreSummary, score_band
from lead_engine.domain.shortlist import DailyShortlistRun, NextAction, SuppressionReason
from lead_engine.infrastructure.contact_providers import ManualContactProvider
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.orm import LeadRow
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import upgrade_database
from lead_engine.infrastructure.website.html_analyzer import BeautifulSoupHtmlAnalyzer


def create_shortlist_fixtures(engine: Engine) -> tuple[UUID, ...]:
    leads = list(create_fixtures(engine))
    # Two synthetic health clones for timing/suppression examples, not discovery.
    with SqlAlchemyUnitOfWork(engine) as uow:
        base = leads[0]
        source = Source(
            source_type=SourceType.MANUAL,
            url="https://fixtures.example/facts",
            metadata={"fixture": True},
        )
        uow.repository.add(source)
        signals = uow.repository.list(Evidence, company_id=base.company_id)
        for name in ("Lost Health", "Suppressed Health"):
            company = Company(
                canonical_name=name, website=f"https://{name.split()[0].lower()}.example/"
            )
            campaign = Campaign(name=name, campaign_type=CampaignType.HEALTH)
            lead = Lead(company_id=company.id, campaign_id=campaign.id)
            for entity in (company, campaign, lead):
                uow.repository.add(entity)
            for e in signals:
                if (
                    e.evidence_type in BusinessSignal.__members__
                    and e.evidence_type != "DECISION_MAKER_ACCESS"
                ):
                    LeadService(uow).add_evidence(
                        Evidence(
                            company_id=company.id,
                            source_id=source.id,
                            evidence_type=e.evidence_type,
                            statement=e.statement,
                            raw_value=e.raw_value,
                            confidence=e.confidence,
                            observed_at=utc_now(),
                        )
                    )
            leads.append(lead)
        uow.commit()
    auditor = WebsiteAuditService(
        lambda: SqlAlchemyUnitOfWork(engine), FixtureFetcher(), BeautifulSoupHtmlAnalyzer()
    )
    for lead in leads[4:]:
        auditor.audit_company(lead.company_id)
    finder = DecisionMakerResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    for index, lead in enumerate(leads):
        with SqlAlchemyUnitOfWork(engine) as uow:
            existing = uow.repository.get(Company, lead.company_id)
            assert existing
            company = Company.model_validate(
                {
                    **existing.model_dump(),
                    "city": "Lima",
                    "country": "Peru",
                    "industry": "Synthetic fixture industry",
                    "updated_at": utc_now(),
                }
            )
            uow.repository.update_company(company)
            source = Source(
                source_type=SourceType.MANUAL,
                url="https://fixtures.example/observations",
                metadata={"fixture": True},
            )
            uow.repository.add(source)
            if index == 1:
                for kind, value in (("GOOGLE_REVIEW_COUNT", 500), ("GOOGLE_RATING", "4.8")):
                    LeadService(uow).add_evidence(
                        Evidence(
                            company_id=company.id,
                            source_id=source.id,
                            evidence_type=kind,
                            statement=f"Synthetic {kind}",
                            raw_value=value,
                            confidence=0.9,
                            observed_at=utc_now(),
                        )
                    )
            if index == 3:
                for kind in ("HIGH_VALUE_SERVICE", "HAS_RESERVATION_PATH"):
                    LeadService(uow).add_evidence(
                        Evidence(
                            company_id=company.id,
                            source_id=source.id,
                            evidence_type=kind,
                            statement=f"Synthetic {kind}",
                            raw_value=kind == "HIGH_VALUE_SERVICE",
                            confidence=0.9,
                            observed_at=utc_now(),
                        )
                    )
            uow.commit()
        if index not in {1, 3}:
            finder.research(
                company.id,
                ManualContactProvider(
                    [
                        ContactCandidate(
                            full_name="Synthetic Owner",
                            role_title="Owner",
                            role_category=RoleCategory.OWNER,
                            company_id=company.id,
                            source_url=(company.website or "https://fixtures.example/") + "team",
                            source_type=SourceType.WEBSITE,
                            association_statement=(
                                "Synthetic team page explicitly identifies Synthetic Owner as Owner"
                            ),
                            linkedin_url=f"https://linkedin.com/in/fixture-{index}",
                            confidence=0.9,
                        )
                    ]
                ),
                lead.id,
                force=True,
            )
    with Session(engine) as session:
        for index, lead in enumerate(leads):
            row = session.get(LeadRow, lead.id)
            assert row
            row.status = (
                LeadStatus.LOST
                if index == 4
                else LeadStatus.CONTACTED
                if index == 2
                else LeadStatus.QUALIFIED
            )
        session.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        for lead, points in zip(leads, (91, 88, 86, 75, 95, 91), strict=True):
            identity = uuid4()
            calculated = utc_now()
            summary = ScoreSummary(
                band=score_band(Decimal(points), (85, 70, 55, 40)),
                completeness_percent=100,
                known_weight=100,
                input_cutoff=calculated.isoformat(),
            )
            uow.repository.add(
                LeadScore(
                    id=identity,
                    lead_id=lead.id,
                    total_score=Decimal(points),
                    calculated_at=calculated,
                    scoring_version="shortlist-fixture-v1",
                    explanation=summary.model_dump_json(),
                    components=(
                        ScoreComponent(
                            lead_score_id=identity,
                            criterion="synthetic scenario",
                            points_awarded=Decimal(points),
                            max_points=Decimal(100),
                            explanation="Explicit synthetic acceptance score",
                        ),
                    ),
                )
            )
        uow.repository.add(
            LeadInteraction(
                lead_id=leads[2].id,
                interaction_type=InteractionType.EMAIL,
                occurred_at=utc_now() - timedelta(days=1),
                notes="Synthetic outreach yesterday",
            )
        )
        uow.commit()
    research = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    for lead in leads:
        research.research_lead(lead.id)
    shortlist = DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine))
    shortlist.suppress(leads[5].id, 14, "Waiting for referral")
    return tuple(lead.id for lead in leads)


def run_acceptance(engine: Engine) -> DailyShortlistRun:
    ids = create_shortlist_fixtures(engine)
    service = DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine))
    run = service.today()
    assert [item.lead_id for item in run.items] == [ids[0], ids[1], ids[3]]
    assert [item.recommended_next_action for item in run.items] == [
        NextAction.SEND_LINKEDIN_CONNECTION,
        NextAction.FIND_DECISION_MAKER,
        NextAction.RESEARCH_MORE,
    ]
    suppressed = {d.item.lead_id: d.suppression_reasons for d in run.suppressed}
    assert SuppressionReason.RECENT_CONTACT in suppressed[ids[2]]
    assert SuppressionReason.LOST in suppressed[ids[4]]
    assert SuppressionReason.MANUAL_SUPPRESSION in suppressed[ids[5]]
    return run


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        url = f"sqlite+pysqlite:///{Path(directory) / 'shortlist.db'}"
        upgrade_database(url)
        engine = build_engine(url)
        try:
            print(render_run(run_acceptance(engine)))
        finally:
            engine.dispose()
