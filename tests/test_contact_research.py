"""Offline contact research policy, provenance, persistence and CLI tests."""

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine
from typer.testing import CliRunner

from examples.contact_fixture_demo import run_acceptance
from lead_engine.application.contact_research import DecisionMakerResearchService
from lead_engine.application.website import FetchResult
from lead_engine.cli.main import app
from lead_engine.domain.contact_research import (
    ContactCandidate,
    ContactIdentity,
    DecisionMakerResearchRun,
    VerificationState,
    role_points,
    source_reliability,
)
from lead_engine.domain.enums import CampaignType, RoleCategory, SourceType
from lead_engine.domain.errors import IdentityConflictError
from lead_engine.domain.models import Campaign, Company, Contact, Evidence, Lead, Source, utc_now
from lead_engine.infrastructure.contact_providers import (
    CompanyWebsiteContactProvider,
    ManualContactProvider,
    StaticPublicSearchProvider,
)
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork


def setup(
    engine: Engine, kind: CampaignType = CampaignType.HEALTH
) -> tuple[Company, Lead, DecisionMakerResearchService]:
    company = Company(canonical_name="Clinic", website="https://clinic.example/")
    campaign = Campaign(name="Campaign", campaign_type=kind)
    lead = Lead(company_id=company.id, campaign_id=campaign.id)
    with SqlAlchemyUnitOfWork(engine) as uow:
        for entity in (company, campaign, lead):
            uow.repository.add(entity)
        uow.commit()
    return company, lead, DecisionMakerResearchService(lambda: SqlAlchemyUnitOfWork(engine))


def candidate(company: Company, **changes: object) -> ContactCandidate:
    data: dict[str, object] = dict(
        full_name="Ana Perez",
        role_title="General Manager",
        role_category=RoleCategory.GENERAL_MANAGEMENT,
        company_id=company.id,
        source_url="https://clinic.example/team",
        source_type=SourceType.WEBSITE,
        association_statement="Clinic identifies Ana Perez as General Manager",
        confidence=0.9,
    )
    data.update(changes)
    return ContactCandidate.model_validate(data)


def test_manual_roundtrip_provenance_channels(engine: Engine) -> None:
    company, lead, service = setup(engine)
    item = candidate(
        company,
        public_email="ana@example.com",
        public_phone="+51 123456789",
        linkedin_url="https://linkedin.com/in/ana",
    )
    run = service.research(company.id, ManualContactProvider([item]), lead.id)
    assert run.contacts_created == 1
    result = service.recommend_contacts(lead.id)
    contact = result.contacts[0]
    assert contact.verification == VerificationState.SUPPORTED and contact.fit_score == 96
    assert {c.channel_type for c in contact.channels} == {"LINKEDIN", "EMAIL", "PHONE"}
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(DecisionMakerResearchRun, run.id) == run
        assert len(uow.repository.list(Source)) == 1
        assert len(uow.repository.list(Evidence)) == 5
        assert all(uow.repository.get(Evidence, eid) is not None for eid in contact.evidence_ids)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_url": ""},
        {"source_url": "file:///secret"},
        {"public_email": "guessed"},
        {"confidence": 1.1},
        {"observed_at": "2020-01-01T00:00:00"},
    ],
)
def test_candidate_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        candidate(Company(canonical_name="Clinic"), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"association_statement": None},
        {"source_type": SourceType.GOOGLE_SEARCH},
        {"role_title": None},
        {"confidence": 0.3},
        {"observed_at": utc_now() + timedelta(days=1)},
    ],
)
def test_unverified_not_recommended(engine: Engine, changes: dict[str, object]) -> None:
    company, lead, service = setup(engine)
    service.research(company.id, ManualContactProvider([candidate(company, **changes)]))
    result = service.recommend_contacts(lead.id)
    assert not result.contacts and result.target_roles


def test_dedup_history_and_stale(engine: Engine) -> None:
    company, lead, service = setup(engine)
    observed = utc_now() - timedelta(days=40)
    old = candidate(company, linkedin_url="https://linkedin.com/in/ana/", observed_at=observed)
    provider = ManualContactProvider([old])
    run = service.research(company.id, provider)
    assert service.recommend_contacts(lead.id).contacts[0].verification == VerificationState.STALE
    assert service.research(company.id, provider).id == run.id
    new = candidate(
        company,
        role_title="Marketing Manager",
        role_category=RoleCategory.MARKETING,
        linkedin_url="https://linkedin.com/in/ana",
        observed_at=utc_now(),
    )
    run2 = service.research(company.id, ManualContactProvider([new]), force=True)
    assert run2.contacts_created == 0 and run2.contacts_reused == 1 and run2.conflicts == 1
    recommendation = service.recommend_contacts(lead.id).contacts[0]
    assert recommendation.current_role == "Marketing Manager" and recommendation.warnings
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert len(uow.repository.list(Contact)) == 1
        assert len(uow.repository.list(Evidence, evidence_type="CONTACT_ROLE")) == 2
        assert uow.repository.list(Contact)[0].role_title == "General Manager"


def test_same_date_conflict(engine: Engine) -> None:
    company, lead, service = setup(engine)
    observed = utc_now()
    for role, category in [
        ("Owner", RoleCategory.OWNER),
        ("Marketing Manager", RoleCategory.MARKETING),
    ]:
        service.research(
            company.id,
            ManualContactProvider(
                [
                    candidate(
                        company,
                        role_title=role,
                        role_category=category,
                        public_email="ana@example.com",
                        observed_at=observed,
                    )
                ]
            ),
            force=True,
        )
    assert (
        service.recommend_contacts(lead.id).contacts[0].verification == VerificationState.CONFLICTED
    )


def test_same_name_other_company_not_merged(engine: Engine) -> None:
    company, _, service = setup(engine)
    other = Company(canonical_name="Other")
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(other)
        uow.commit()
    for c in (company, other):
        service.research(c.id, ManualContactProvider([candidate(c)]), force=True)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert len(uow.repository.list(Contact)) == 2


def test_rollback_on_conflicting_identity(engine: Engine) -> None:
    company, _, service = setup(engine)
    service.research(
        company.id, ManualContactProvider([candidate(company, public_email="ana@example.com")])
    )
    with pytest.raises(IdentityConflictError):
        service.research(
            company.id,
            ManualContactProvider(
                [
                    candidate(company, full_name="Luis Soto"),
                    candidate(company, full_name="Pedro Diaz", public_email="ana@example.com"),
                ]
            ),
            force=True,
        )
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert len(uow.repository.list(Contact)) == 1
        assert len(uow.repository.list(Source)) == 1
        assert len(uow.repository.list(DecisionMakerResearchRun)) == 1


def test_exact_name_compatible_role(engine: Engine) -> None:
    company, _, service = setup(engine)
    for name in ("Ana Pérez", "ANA PEREZ"):
        service.research(
            company.id, ManualContactProvider([candidate(company, full_name=name)]), force=True
        )
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert len(uow.repository.list(Contact)) == 1
        assert uow.repository.list(ContactIdentity)


@pytest.mark.parametrize(
    "kind,category,expected",
    [
        (CampaignType.HEALTH, RoleCategory.MEDICAL_DIRECTOR, 42),
        (CampaignType.CONSTRUCTION, RoleCategory.COMMERCIAL, 44),
        (CampaignType.HOSPITALITY, RoleCategory.COMMERCIAL, 35),
    ],
)
def test_campaign_fit(kind: CampaignType, category: RoleCategory, expected: int) -> None:
    assert role_points(category, kind, None, "Role") == expected


def test_context_fit() -> None:
    assert role_points(RoleCategory.OWNER, CampaignType.HOSPITALITY, "independent", "Owner") == 50
    assert (
        role_points(RoleCategory.MARKETING, CampaignType.HOSPITALITY, "group", "Brand Manager")
        == 50
    )
    assert (
        role_points(
            RoleCategory.GENERAL_MANAGEMENT, CampaignType.HOSPITALITY, "group", "Restaurant Manager"
        )
        == 25
    )
    assert (
        role_points(
            RoleCategory.MEDICAL_DIRECTOR, CampaignType.HEALTH, "small_clinic", "Medical Director"
        )
        == 50
    )


@pytest.mark.parametrize(
    "kind,points",
    [
        (SourceType.GOVERNMENT_REGISTRY, 9),
        (SourceType.LINKEDIN_PUBLIC, 8),
        (SourceType.GOOGLE_SEARCH, 2),
        (SourceType.MANUAL, 6),
    ],
)
def test_reliability(kind: SourceType, points: int) -> None:
    company = Company(canonical_name="Clinic", website="https://clinic.example/")
    assert (
        source_reliability(
            candidate(company, source_url="https://public.example/", source_type=kind), company
        )
        == points
    )
    assert source_reliability(candidate(company, source_type=SourceType.MANUAL), company) == 10


@pytest.mark.parametrize(
    "line,category",
    [
        ("Gerente General: Ana Pérez", RoleCategory.GENERAL_MANAGEMENT),
        ("Director Médico: Dr. Juan García", RoleCategory.MEDICAL_DIRECTOR),
        ("Marketing Manager – María López", RoleCategory.MARKETING),
        ("Owner: John Smith", RoleCategory.OWNER),
    ],
)
def test_es_en_patterns(line: str, category: RoleCategory) -> None:
    company = Company(canonical_name="Clinic")
    result = CompanyWebsiteContactProvider.extract(
        f"<p>{line}</p>", "https://clinic.example/team", company
    )
    assert len(result) == 1 and result[0].role_category == category
    assert result[0].public_email is None and result[0].public_phone is None


def test_structured_person() -> None:
    company = Company(canonical_name="Clinic")
    html = (
        '<script type="application/ld+json">{"@type":"Person","name":"Ana Perez",'
        '"jobTitle":"Marketing Manager","worksFor":{"name":"Clinic"},'
        '"email":"ana@example.com"}</script>'
    )
    result = CompanyWebsiteContactProvider.extract(html, "https://clinic.example/", company)
    assert result[0].public_email == "ana@example.com"
    assert not CompanyWebsiteContactProvider.extract(
        html.replace('"Clinic"', '"Other"'), "https://clinic.example/", company
    )


class FixtureFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            body=self.pages.get(url, ""),
            content_type="text/html",
        )


def test_bounded_one_level_same_domain() -> None:
    company = Company(canonical_name="Clinic", website="https://clinic.example/")
    fetcher = FixtureFetcher(
        {
            company.website
            or "": '<a href="/team">Team</a><a href="https://external.example/team">Team</a>',
            "https://clinic.example/team": '<p>Owner: Ana Perez</p><a href="/staff">Staff</a>',
        }
    )
    provider = CompanyWebsiteContactProvider(fetcher)
    assert len(provider.discover(company)) == 1
    assert fetcher.calls == [company.website, "https://clinic.example/team"]


def test_no_guesses_or_hidden_people() -> None:
    company = Company(canonical_name="Clinic")
    html = (
        "<p>Contact our marketing team</p><p hidden>Owner: Ana Perez</p>"
        "<p>General Manager</p><p>Juan Garcia</p>"
    )
    assert not CompanyWebsiteContactProvider.extract(html, "https://clinic.example/", company)


def test_acceptance(engine: Engine) -> None:
    results = run_acceptance(engine)
    assert [r.contacts[0].fit_score for r in results[:4]] == [86, 90, 90, 90]
    assert not results[4].contacts


def test_campaign_threshold_freshness_force(engine: Engine) -> None:
    company, lead, service = setup(engine)
    provider = ManualContactProvider([candidate(company)])
    assert not service.research_campaign(lead.campaign_id, provider, min_lead_score=70)
    first = service.research_campaign(lead.campaign_id, provider)[0]
    assert service.research_campaign(lead.campaign_id, provider)[0].id == first.id
    assert service.research_campaign(lead.campaign_id, provider, force=True)[0].id != first.id


def test_static_search_port() -> None:
    item = candidate(Company(canonical_name="Clinic"), source_type=SourceType.GOOGLE_SEARCH)
    assert StaticPublicSearchProvider({"query": [item]}).search("query") == [item]


def test_cli(engine: Engine, database_url: str) -> None:
    company, lead, _ = setup(engine)
    runner = CliRunner()
    options = ["--database-url", database_url]
    args = [
        "contact",
        "add",
        "--company-id",
        str(company.id),
        "--name",
        "Ana Perez",
        "--role",
        "General Manager",
        "--role-category",
        "GENERAL_MANAGEMENT",
        "--source-url",
        "https://clinic.example/team",
        "--association-statement",
        "Clinic identifies Ana Perez as General Manager",
        *options,
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    for command, arguments in [
        ("list", ["--company-id", str(company.id)]),
        ("runs", []),
        ("recommend", ["--lead-id", str(lead.id)]),
        ("recommend", ["--campaign", "Campaign"]),
    ]:
        result = runner.invoke(app, ["contact", command, *arguments, *options])
        assert result.exit_code == 0, result.output
    assert runner.invoke(app, ["contact", "recommend", *options]).exit_code == 1


@pytest.mark.parametrize("mode", ["ssrf", "robots", "redirect", "auth"])
def test_safe_http_reused(mode: str) -> None:
    import httpx

    from lead_engine.application.website import AuditSettings
    from lead_engine.infrastructure.website.http_fetcher import HttpxFetcher
    from lead_engine.infrastructure.website.security import PublicUrlPolicy

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /"
                if mode == "robots"
                else "User-agent: *\nDisallow:",
            )
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://external.example/team"})
        if mode == "auth":
            return httpx.Response(403)
        return httpx.Response(
            200, text="<p>Owner: Ana Perez</p>", headers={"content-type": "text/html"}
        )

    fetcher = HttpxFetcher(
        AuditSettings(min_request_interval_seconds=0),
        policy=PublicUrlPolicy(
            lambda host, port: ["127.0.0.1" if mode == "ssrf" else "93.184.216.34"]
        ),
        transport=httpx.MockTransport(handler),
        same_domain_only=True,
    )
    try:
        provider = CompanyWebsiteContactProvider(fetcher)
        assert not provider.discover(
            Company(canonical_name="Clinic", website="https://clinic.example/")
        )
        assert provider.warnings
        assert not any("external.example" in url for url in calls)
        if mode == "ssrf":
            assert not calls
        if mode == "robots":
            assert len(calls) == 1
    finally:
        fetcher.close()


def test_migration_preserves_existing_contacts(database_url: str) -> None:
    from alembic import command

    from lead_engine.infrastructure.database import build_engine
    from lead_engine.infrastructure.schema import (
        database_revision,
        migration_config,
        upgrade_database,
    )

    command.upgrade(migration_config(database_url), "0003")
    engine = build_engine(database_url)
    try:
        company = Company(canonical_name="Before migration")
        contact = Contact(company_id=company.id, full_name="Ana Perez")
        with SqlAlchemyUnitOfWork(engine) as uow:
            uow.repository.add(company)
            uow.repository.add(contact)
            uow.commit()
        upgrade_database(database_url)
        assert database_revision(engine) == "0008"
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.get(Contact, contact.id) == contact
        command.downgrade(migration_config(database_url), "0003")
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.get(Contact, contact.id) == contact
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "changes",
    [{"context": "group"}, {"company_id": uuid4()}, {"company_name": "Different Company"}],
)
def test_invalid_context_or_company(engine: Engine, changes: dict[str, object]) -> None:
    company, _, service = setup(engine)
    with pytest.raises((ValidationError, IdentityConflictError)):
        service.research(company.id, ManualContactProvider([candidate(company, **changes)]))


def test_profile_collision_requires_review(engine: Engine) -> None:
    company, _, service = setup(engine)
    for index in range(2):
        provider = ManualContactProvider(
            [candidate(company, linkedin_url=f"https://linkedin.com/in/ana-{index}")]
        )
        if index == 0:
            service.research(company.id, provider)
        else:
            with pytest.raises(IdentityConflictError):
                service.research(company.id, provider, force=True)


def test_latest_score_filters_batch(engine: Engine) -> None:
    from decimal import Decimal

    from lead_engine.domain.models import LeadScore, ScoreComponent

    company, lead, service = setup(engine)
    for index, points in enumerate([90, 20]):
        identity = uuid4()
        score = LeadScore(
            id=identity,
            lead_id=lead.id,
            total_score=Decimal(points),
            scoring_version="fixture-v1",
            calculated_at=utc_now() + timedelta(seconds=index),
            components=(
                ScoreComponent(
                    lead_score_id=identity,
                    criterion="fixture",
                    points_awarded=Decimal(points),
                    max_points=Decimal(100),
                    explanation="Offline qualification fixture",
                ),
            ),
        )
        with SqlAlchemyUnitOfWork(engine) as uow:
            uow.repository.add(score)
            uow.commit()
    assert not service.research_campaign(
        lead.campaign_id, ManualContactProvider([candidate(company)]), 70
    )
    assert (
        len(
            service.research_campaign(
                lead.campaign_id, ManualContactProvider([candidate(company)]), 10
            )
        )
        == 1
    )


def test_failed_provider_logged(engine: Engine) -> None:
    class FailedProvider(ManualContactProvider):
        def discover(self, company: Company) -> list[ContactCandidate]:
            raise OSError("fixture failure")

    company, _, service = setup(engine)
    result = service.research(company.id, FailedProvider([]))
    assert result.status == "FAILED"
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert not uow.repository.list(Contact)
        assert uow.repository.get(DecisionMakerResearchRun, result.id) == result


def test_no_linkedin_requests() -> None:
    fetcher = FixtureFetcher({})
    provider = CompanyWebsiteContactProvider(fetcher)
    assert not provider.discover(
        Company(canonical_name="Clinic", website="https://linkedin.com/company/clinic")
    )
    assert not fetcher.calls and provider.warnings


def test_recommendation_deterministic(engine: Engine) -> None:
    company, lead, service = setup(engine)
    service.research(company.id, ManualContactProvider([candidate(company)]))
    assert service.recommend_contacts(lead.id) == service.recommend_contacts(lead.id)


def test_conflicting_titles_in_same_category(engine: Engine) -> None:
    company, lead, service = setup(engine)
    observed = utc_now()
    for title in ("Marketing Analyst", "Marketing Manager"):
        service.research(
            company.id,
            ManualContactProvider(
                [
                    candidate(
                        company,
                        role_title=title,
                        role_category=RoleCategory.MARKETING,
                        observed_at=observed,
                    )
                ]
            ),
            force=True,
        )
    result = service.recommend_contacts(lead.id)
    assert result.contacts[0].verification == VerificationState.CONFLICTED
    assert result.contacts[0].warnings
