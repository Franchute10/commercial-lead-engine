import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from lead_engine.application.auditor import WebsiteAuditService
from lead_engine.application.services import LeadService
from lead_engine.application.website import AuditSettings
from lead_engine.cli.main import app
from lead_engine.domain.audit import AuditStatus, FindingType, WebsiteAudit
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.models import Campaign, Company, Evidence, Lead, Source
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.website.html_analyzer import BeautifulSoupHtmlAnalyzer
from lead_engine.infrastructure.website.http_fetcher import HttpxFetcher
from lead_engine.infrastructure.website.security import PublicUrlPolicy

FIXTURES = Path(__file__).parent / "fixtures" / "website"


def public_dns(host: str, port: int) -> Sequence[str]:
    return ["93.184.216.34"]


def fetcher_for(html: str, *, status: int = 200, https_redirect: bool = False) -> HttpxFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if https_redirect and request.url.scheme == "http":
            return httpx.Response(301, headers={"Location": "https://site.example/"})
        return httpx.Response(status, text=html, headers={"Content-Type": "text/html"})

    return HttpxFetcher(
        AuditSettings(min_request_interval_seconds=0),
        policy=PublicUrlPolicy(public_dns),
        transport=httpx.MockTransport(handler),
    )


def company(engine: Engine, website: str | None = "https://site.example/") -> Company:
    with SqlAlchemyUnitOfWork(engine) as uow:
        value = LeadService(uow).upsert_company(Company(canonical_name="Demo", website=website))
        uow.commit()
        return value


def service(
    engine: Engine, fetcher: HttpxFetcher, settings: AuditSettings | None = None
) -> WebsiteAuditService:
    return WebsiteAuditService(
        lambda: SqlAlchemyUnitOfWork(engine), fetcher, BeautifulSoupHtmlAnalyzer(), settings
    )


def test_booking_signals_es_en_and_metadata() -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        (FIXTURES / "booking.html").read_text(encoding="utf-8"), "https://site.example/"
    )
    kinds = {value.kind for value in analysis.findings}
    assert {
        FindingType.PAGE_TITLE_PRESENT,
        FindingType.META_DESCRIPTION_PRESENT,
        FindingType.MOBILE_VIEWPORT_PRESENT,
        FindingType.H1_PRESENT,
        FindingType.MULTIPLE_H1,
        FindingType.BOOKING_CTA_PRESENT,
        FindingType.QUOTE_CTA_PRESENT,
        FindingType.SERVICES_PAGE_PRESENT,
        FindingType.PRIVACY_POLICY_PRESENT,
        FindingType.RESERVATION_PROVIDER_PRESENT,
        FindingType.ADDRESS_PRESENT,
        FindingType.BUSINESS_HOURS_PRESENT,
        FindingType.HAS_RESERVATION_PATH,
        FindingType.HAS_CONVERSION_CTA,
    } <= kinds
    english = BeautifulSoupHtmlAnalyzer().analyze(
        '<a href="/book">Book now</a><a href="/quote">Request a quote</a>', "https://site.example"
    )
    assert {FindingType.BOOKING_CTA_PRESENT, FindingType.QUOTE_CTA_PRESENT} <= {
        item.kind for item in english.findings
    }


def test_contact_and_catalog_signals() -> None:
    analyzer = BeautifulSoupHtmlAnalyzer()
    contact = analyzer.analyze(
        (FIXTURES / "contact.html").read_text(encoding="utf-8"), "https://site.example/"
    )
    assert {
        FindingType.PHONE_LINK_PRESENT,
        FindingType.EMAIL_LINK_PRESENT,
        FindingType.WHATSAPP_LINK_PRESENT,
        FindingType.CONTACT_FORM_PRESENT,
        FindingType.INSTAGRAM_LINK_PRESENT,
        FindingType.FACEBOOK_LINK_PRESENT,
        FindingType.LINKEDIN_LINK_PRESENT,
        FindingType.SOCIAL_LINK_PRESENT,
        FindingType.MAP_LINK_PRESENT,
        FindingType.HAS_DIRECT_CONTACT_PATH,
    } <= {item.kind for item in contact.findings}
    catalog = analyzer.analyze(
        (FIXTURES / "catalog.html").read_text(encoding="utf-8"), "https://site.example/"
    )
    assert {
        FindingType.CATALOG_PRESENT,
        FindingType.PRODUCTS_PAGE_PRESENT,
        FindingType.ECOMMERCE_PRESENT,
        FindingType.HAS_PRODUCT_DISCOVERY_PATH,
    } <= {item.kind for item in catalog.findings}


def test_no_false_book_from_facebook_and_no_search_form_or_hidden_signals() -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        '<a href="https://facebook.com/demo">Facebook</a>'
        '<script>Reservar cotizar</script><p>Our book collection</p><a hidden href="/book">Book</a>'
        '<form><input type="search"></form><a href="https://instagram.com.evil.example">Social</a>',
        "https://site.example",
    )
    kinds = {item.kind for item in analysis.findings}
    assert FindingType.FACEBOOK_LINK_PRESENT in kinds
    assert (
        not {
            FindingType.BOOKING_CTA_PRESENT,
            FindingType.CONTACT_FORM_PRESENT,
            FindingType.INSTAGRAM_LINK_PRESENT,
        }
        & kinds
    )


def test_audit_persistence_provenance_freshness_force_and_http(engine: Engine) -> None:
    stored = company(engine, "http://site.example/")
    fetcher = fetcher_for(
        (FIXTURES / "booking.html").read_text(encoding="utf-8"), https_redirect=True
    )
    try:
        auditor = service(engine, fetcher)
        result = auditor.audit_company(stored.id)
        assert result.audit.status == AuditStatus.SUCCESS and result.reachable
        assert {
            FindingType.HTTPS_ENABLED,
            FindingType.REDIRECT_PRESENT,
            FindingType.WEBSITE_REACHABLE,
        } <= {item.kind for item in result.findings}
        repeated = auditor.audit_company(stored.id)
        assert (
            repeated.skipped_fresh
            and repeated.evidence_created == 0
            and repeated.audit.id == result.audit.id
        )
        forced = auditor.audit_company(stored.id, force=True)
        assert forced.audit.id != result.audit.id and forced.evidence_created > 0
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.get(WebsiteAudit, result.audit.id) == result.audit
            source = uow.repository.get(Source, result.audit.source_id)
            assert (
                source is not None
                and source.url == stored.website
                and source.title == "Clínica Demo"
            )
            assert (
                source.metadata is not None
                and source.metadata["final_url"] == "https://site.example/"
            )
            evidence = uow.repository.list(Evidence, website_audit_id=result.audit.id)
            assert len(evidence) == result.evidence_created
            assert all(
                item.company_id == stored.id and item.source_id == source.id for item in evidence
            )
            assert (
                result.audit.finished_at is not None
                and result.audit.finished_at.utcoffset() == timedelta(0)
            )
    finally:
        fetcher.close()


def test_no_website_has_traceable_evidence_and_no_network(engine: Engine) -> None:
    stored = company(engine, None)
    fetcher = fetcher_for("")
    try:
        result = service(engine, fetcher).audit_company(stored.id)
        assert result.audit.status == AuditStatus.NO_WEBSITE and result.evidence_created == 1
        assert result.findings[0].kind == FindingType.NO_WEBSITE
        with SqlAlchemyUnitOfWork(engine) as uow:
            source = uow.repository.get(Source, result.audit.source_id)
            assert source is not None and source.url is None
            assert uow.repository.list(Evidence)[0].website_audit_id == result.audit.id
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "html,status,expected",
    [
        ("missing", 404, AuditStatus.FAILED),
        ("not HTML markup", 200, AuditStatus.PARTIAL),
        ("<title>Access denied</title>", 200, AuditStatus.FAILED),
        ('<form><input type="password"></form>', 200, AuditStatus.FAILED),
        ("<h1>Unclosed", 200, AuditStatus.SUCCESS),
    ],
)
def test_failed_partial_and_tolerant_html_audits(
    engine: Engine, html: str, status: int, expected: AuditStatus
) -> None:
    fetcher = fetcher_for(html, status=status)
    try:
        result = service(engine, fetcher).audit_company(company(engine).id)
        assert result.audit.status == expected
        if expected == AuditStatus.FAILED:
            assert FindingType.FETCH_ERROR in {item.kind for item in result.findings}
    finally:
        fetcher.close()


def test_http_https_missing_and_slow_threshold(engine: Engine) -> None:
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        result = service(engine, fetcher, AuditSettings(slow_response_ms=0.000001)).audit_company(
            company(engine, "http://site.example").id
        )
        assert {FindingType.HTTPS_MISSING, FindingType.SLOW_RESPONSE} <= {
            item.kind for item in result.findings
        }
    finally:
        fetcher.close()


def test_freshness_zero_disables_reuse(engine: Engine) -> None:
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        stored = company(engine)
        auditor = service(engine, fetcher, AuditSettings(freshness_days=0))
        first, second = auditor.audit_company(stored.id), auditor.audit_company(stored.id)
        assert first.audit.id != second.audit.id
    finally:
        fetcher.close()


def test_campaign_sequential_limit_no_website_skips_and_force(engine: Engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        writer = LeadService(uow)
        campaign = writer.create_campaign(
            Campaign(name="Health", campaign_type=CampaignType.HEALTH)
        )
        for index in range(3):
            stored = writer.upsert_company(
                Company(
                    canonical_name=f"Clinic {index}",
                    website=f"https://site{index}.example" if index < 2 else None,
                )
            )
            writer.create_lead(Lead(company_id=stored.id, campaign_id=campaign.id))
        uow.commit()
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        auditor = service(engine, fetcher)
        first = auditor.audit_campaign(campaign.id, limit=1)
        assert len(first.results) == 1 and first.skipped_no_website == 1
        second = auditor.audit_campaign(campaign.id, limit=2)
        assert sum(item.skipped_fresh for item in second.results) == 1
        assert sum(not item.skipped_fresh for item in second.results) == 1
        forced = auditor.audit_campaign(campaign.id, limit=2, force=True)
        assert len(forced.results) == 2 and all(not item.skipped_fresh for item in forced.results)
    finally:
        fetcher.close()


def test_audit_source_and_all_evidence_rollback_together(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = company(engine)
    original = LeadService.add_evidence

    def fail_after_insert(self: LeadService, evidence: Evidence) -> Evidence:
        original(self, evidence)
        raise RuntimeError("Simulated evidence write failure")

    monkeypatch.setattr(LeadService, "add_evidence", fail_after_insert)
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        with pytest.raises(RuntimeError):
            service(engine, fetcher).audit_company(stored.id)
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.list(WebsiteAudit) == []
            assert uow.repository.list(Source) == [] and uow.repository.list(Evidence) == []
    finally:
        fetcher.close()


def test_cli_company_domain_force_campaign_and_list(
    engine: Engine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lead_engine.cli.audit as cli

    stored = company(engine)
    # Inject mocked HTTP adapter at composition root; production CLI exposes no policy bypass.
    monkeypatch.setattr(cli, "HttpxFetcher", lambda settings: fetcher_for("<h1>Home</h1>"))
    runner = CliRunner()
    options = ["--database-url", database_url]
    result = runner.invoke(app, ["audit", "website", "--company-id", str(stored.id), *options])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["evidence_created"] > 0
    repeated = runner.invoke(app, ["audit", "website", "--domain", "site.example", *options])
    assert repeated.exit_code == 0 and json.loads(repeated.stdout)["skipped_fresh"]
    forced = runner.invoke(
        app, ["audit", "website", "--domain", "site.example", "--force", *options]
    )
    assert forced.exit_code == 0 and not json.loads(forced.stdout)["skipped_fresh"]
    assert runner.invoke(app, ["audit", "list", *options]).exit_code == 0
    assert runner.invoke(app, ["audit", "website", *options]).exit_code == 1
    with SqlAlchemyUnitOfWork(engine) as uow:
        writer = LeadService(uow)
        context = writer.create_campaign(Campaign(name="Health", campaign_type=CampaignType.HEALTH))
        writer.create_lead(Lead(company_id=stored.id, campaign_id=context.id))
        uow.commit()
    assert (
        runner.invoke(app, ["audit", "campaign", "--campaign", "Health", *options]).exit_code == 0
    )


def test_successful_audit_expires_after_seven_days(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lead_engine.application.auditor as module
    from lead_engine.domain.models import utc_now

    stored = company(engine)
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        auditor = service(engine, fetcher)
        first = auditor.audit_company(stored.id)
        future = utc_now() + timedelta(days=8)
        monkeypatch.setattr(module, "utc_now", lambda: future)
        second = auditor.audit_company(stored.id)
        assert not second.skipped_fresh and second.audit.id != first.audit.id
    finally:
        fetcher.close()


def test_migration_preserves_existing_evidence_and_score_associations(
    engine: Engine, database_url: str
) -> None:
    from decimal import Decimal
    from uuid import uuid4

    from alembic import command
    from sqlalchemy import text

    from lead_engine.domain.enums import SourceType
    from lead_engine.domain.models import LeadScore, ScoreComponent
    from lead_engine.infrastructure.schema import migration_config, upgrade_database

    command.downgrade(migration_config(database_url), "0002")
    evidence_id, score_id = uuid4(), uuid4()
    with SqlAlchemyUnitOfWork(engine) as uow:
        writer = LeadService(uow)
        stored = writer.upsert_company(Company(canonical_name="Existing"))
        source = writer.add_source(Source(source_type=SourceType.MANUAL))
        context = writer.create_campaign(
            Campaign(name="Existing", campaign_type=CampaignType.HEALTH)
        )
        lead = writer.create_lead(Lead(company_id=stored.id, campaign_id=context.id))
        # Old schema has no website_audit_id; insert its historical record explicitly.
        uow.repository.session.execute(
            text(
                "INSERT INTO evidence "
                "(id, company_id, source_id, evidence_type, statement, raw_value, "
                "confidence, observed_at, created_at) "
                "VALUES (:id, :company, :source, 'NOTE', 'Existing observation', "
                "NULL, 1, :time, :time)"
            ),
            {
                "id": evidence_id.hex,
                "company": stored.id.hex,
                "source": source.id.hex,
                "time": "2026-10-05 00:00:00.000000",
            },
        )
        score = LeadScore(
            id=score_id,
            lead_id=lead.id,
            total_score=Decimal(50),
            scoring_version="old",
            components=(
                ScoreComponent(
                    lead_score_id=score_id,
                    criterion="old",
                    points_awarded=Decimal(50),
                    max_points=Decimal(100),
                    explanation="Existing",
                    evidence_ids=(evidence_id,),
                ),
            ),
        )
        uow.repository.add(score)
        uow.commit()
    upgrade_database(database_url)
    with SqlAlchemyUnitOfWork(engine) as uow:
        evidence = uow.repository.get(Evidence, evidence_id)
        assert evidence is not None and evidence.website_audit_id is None
        assert uow.repository.get(LeadScore, score_id) == score
    command.downgrade(migration_config(database_url), "0002")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM evidence")) == 1
        assert connection.scalar(text("SELECT count(*) FROM component_evidence")) == 1
