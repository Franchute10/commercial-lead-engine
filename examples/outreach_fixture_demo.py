"""Five synthetic acceptance cases; no network, outbound transport or LLM."""

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from sqlalchemy import Engine

from examples.scoring_fixture_demo import FixtureFetcher, create_fixtures
from lead_engine.application.auditor import WebsiteAuditService
from lead_engine.application.contact_research import DecisionMakerResearchService
from lead_engine.application.outreach import OutreachDraftService
from lead_engine.application.outreach_export import OutreachFormat, export_drafts
from lead_engine.application.research import CommercialResearchService
from lead_engine.application.scoring import CommercialScoringService
from lead_engine.domain.contact_research import ContactCandidate
from lead_engine.domain.enums import RoleCategory, SourceType
from lead_engine.domain.models import Company, Evidence, Lead, Source, utc_now
from lead_engine.domain.outreach import DraftStatus, DraftView, OutreachChannel
from lead_engine.infrastructure.contact_providers import ManualContactProvider
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import upgrade_database
from lead_engine.infrastructure.website.html_analyzer import BeautifulSoupHtmlAnalyzer


def create_outreach_fixtures(engine: Engine) -> tuple[UUID, ...]:
    leads = list(create_fixtures(engine))
    # Distinct independent restaurant, its own identity, sources and evidence.
    with SqlAlchemyUnitOfWork(engine) as uow:
        company = Company(
            canonical_name="Independent restaurant fixture",
            website="https://independent.example/",
            city="Lima",
            country="Peru",
            industry="Restaurant",
        )
        uow.repository.add(company)
        independent = Lead(company_id=company.id, campaign_id=leads[2].campaign_id)
        uow.repository.add(independent)
        source = Source(
            source_type=SourceType.MANUAL,
            title="Independent synthetic fixture",
            url="https://independent.example/about",
        )
        uow.repository.add(source)
        for e in uow.repository.list(Evidence, company_id=leads[2].company_id):
            if e.website_audit_id is None and e.evidence_type != "DECISION_MAKER_ACCESS":
                uow.repository.add(
                    Evidence(
                        company_id=company.id,
                        source_id=source.id,
                        evidence_type=e.evidence_type,
                        statement=e.statement,
                        raw_value=e.raw_value,
                        confidence=0.9,
                        observed_at=utc_now(),
                    )
                )
        uow.commit()
    WebsiteAuditService(
        lambda: SqlAlchemyUnitOfWork(engine), FixtureFetcher(), BeautifulSoupHtmlAnalyzer()
    ).audit_company(company.id)
    leads.insert(3, independent)
    finder = DecisionMakerResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    roles = (
        RoleCategory.GENERAL_MANAGEMENT,
        RoleCategory.COMMERCIAL,
        RoleCategory.MARKETING,
        RoleCategory.OWNER,
    )
    titles = ("General Manager", "Commercial Manager", "Brand Manager", "Owner")
    for index, lead in enumerate(leads[:4]):
        with SqlAlchemyUnitOfWork(engine) as uow:
            current = uow.repository.get(Company, lead.company_id)
            assert current is not None
            updated = Company.model_validate(
                {
                    **current.model_dump(),
                    "country": "Peru",
                    "city": "Lima",
                    "industry": "Synthetic business",
                    "updated_at": utc_now(),
                }
            )
            uow.repository.update_company(updated)
            uow.commit()
        finder.research(
            lead.company_id,
            ManualContactProvider(
                [
                    ContactCandidate(
                        full_name=f"Fixture Person {index}",
                        role_title=titles[index],
                        role_category=roles[index],
                        company_id=lead.company_id,
                        source_url=f"https://business{index}.example/team",
                        source_type=SourceType.WEBSITE,
                        association_statement=(
                            f"Official fixture team names this person as {titles[index]}"
                        ),
                        linkedin_url=f"https://linkedin.com/in/fixture-person-{index}",
                        public_email=f"person{index}@business{index}.example",
                        public_phone=f"+5199900000{index}",
                        confidence=0.9,
                    )
                ]
            ),
            lead.id,
            force=True,
        )
        if index == 3:
            recommendation = finder.recommend_contacts(lead.id).contacts[0]
            with SqlAlchemyUnitOfWork(engine) as uow:
                source = Source(
                    source_type=SourceType.MANUAL,
                    title="Human referral attestation",
                    url="https://independent.example/referral-record",
                )
                uow.repository.add(source)
                uow.repository.add(
                    Evidence(
                        company_id=lead.company_id,
                        source_id=source.id,
                        evidence_type="REFERRAL_PATH",
                        statement="Carlos Pérez explicitly recommended contacting the named owner",
                        raw_value={
                            "contact_id": str(recommendation.contact_id),
                            "referrer_name": "Carlos Pérez",
                            "recommended_contact": True,
                        },
                        confidence=1,
                        observed_at=utc_now(),
                    )
                )
                uow.commit()
    scorer = CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine))
    research = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    for lead in leads:
        scorer.score_lead(lead.id)
        research.research_lead(lead.id)
    return tuple(lead.id for lead in leads)


def run_acceptance(engine: Engine) -> tuple[DraftView, ...]:
    ids = create_outreach_fixtures(engine)
    writer = OutreachDraftService(lambda: SqlAlchemyUnitOfWork(engine))
    views = tuple(writer.draft(lead_id, channel=OutreachChannel.EMAIL) for lead_id in ids[:4])
    assert len({view.draft.body for view in views}) == 4
    assert views[3].draft.referral_evidence_id and "Carlos Pérez" in views[3].draft.body
    assert all(not v.draft.referral_evidence_id for v in views[:3])
    try:
        writer.draft(ids[4])
    except ValueError:
        pass
    else:
        raise AssertionError("Research-first fixture must not produce a draft")
    approved = writer.transition(views[0].draft.id, DraftStatus.APPROVED)
    used = writer.transition(views[0].draft.id, DraftStatus.USED)
    assert approved.status == DraftStatus.APPROVED and used.status == DraftStatus.USED
    assert all(event.interaction_id is None for event in used.events)
    return views


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        url = f"sqlite+pysqlite:///{Path(directory) / 'outreach.db'}"
        upgrade_database(url)
        engine = build_engine(url)
        try:
            print(export_drafts(list(run_acceptance(engine)), OutreachFormat.TEXT))
            print(
                "PASS: research-first excluded; approval sends nothing; use is a human attestation."
            )
        finally:
            engine.dispose()
