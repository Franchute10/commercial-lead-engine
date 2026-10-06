"""Offline research acceptance over existing synthetic scoring/audit fixtures."""

from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import Engine

from examples.scoring_fixture_demo import create_fixtures
from lead_engine.application.contact_research import DecisionMakerResearchService
from lead_engine.application.research import CommercialResearchService
from lead_engine.application.research_export import markdown
from lead_engine.application.scoring import CommercialScoringService
from lead_engine.domain.contact_research import ContactCandidate
from lead_engine.domain.enums import RoleCategory, SourceType
from lead_engine.domain.models import Company, utc_now
from lead_engine.domain.research import BriefStatus, CommercialBrief, OpportunityType
from lead_engine.infrastructure.contact_providers import ManualContactProvider
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import upgrade_database


def run_acceptance(engine: Engine) -> tuple[CommercialBrief, ...]:
    leads = create_fixtures(engine)
    contacts = DecisionMakerResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    for lead in leads[:3]:
        with SqlAlchemyUnitOfWork(engine) as uow:
            company = uow.repository.get(Company, lead.company_id)
            assert company is not None
            company = Company.model_validate(
                {
                    **company.model_dump(),
                    "industry": "Synthetic fixture business",
                    "city": "Lima",
                    "country": "Peru",
                    "updated_at": utc_now(),
                }
            )
            uow.repository.update_company(company)
            uow.commit()
        contacts.research(
            company.id,
            ManualContactProvider(
                [
                    ContactCandidate(
                        full_name="Synthetic Owner",
                        role_title="Owner",
                        role_category=RoleCategory.OWNER,
                        company_id=company.id,
                        source_url=(company.website or "http://fixture.example/") + "team",
                        source_type=SourceType.WEBSITE,
                        association_statement=(
                            "Synthetic official team page explicitly names Synthetic Owner as Owner"
                        ),
                        public_profile_url="https://profiles.example/owner",
                        confidence=0.9,
                    )
                ]
            ),
            lead.id,
            force=True,
        )
    scorer = CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine))
    for lead in leads:
        scorer.score_lead(lead.id)
    research = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    briefs = tuple(research.research_lead(lead.id) for lead in leads)
    assert [b.opportunity_type for b in briefs] == [
        OpportunityType.APPOINTMENT_CONVERSION,
        OpportunityType.QUOTE_CONVERSION,
        OpportunityType.RESERVATION_CONVERSION,
        OpportunityType.NO_CLEAR_OPPORTUNITY,
    ]
    assert all(b.decision_maker_recommendations for b in briefs[:3])
    assert briefs[3].status == BriefStatus.INSUFFICIENT_DATA and briefs[3].target_roles
    for brief in briefs:
        assert all(
            o.supporting_evidence_ids
            for o in ((brief.primary_opportunity,) if brief.primary_opportunity else ())
            + brief.secondary_opportunities
        )
    research.research_lead(leads[0].id)
    assert len(research.history(leads[0].id)) == 2
    assert research.history(leads[0].id)[1] == briefs[0]
    return briefs


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        url = f"sqlite+pysqlite:///{Path(directory) / 'research.db'}"
        upgrade_database(url)
        engine = build_engine(url)
        try:
            for brief in run_acceptance(engine):
                print(markdown(brief))
        finally:
            engine.dispose()
