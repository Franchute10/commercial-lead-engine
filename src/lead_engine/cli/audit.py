"""Website audit CLI; all network adapters are composed here."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.auditor import WebsiteAuditService
from lead_engine.application.website import AuditSettings, WebsiteAuditResult
from lead_engine.cli.common import DatabaseOption, admin_errors, transaction
from lead_engine.cli.scout import resolve_campaign
from lead_engine.domain.audit import AuditStatus, WebsiteAudit
from lead_engine.domain.identity import normalize_domain
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.website.html_analyzer import BeautifulSoupHtmlAnalyzer
from lead_engine.infrastructure.website.http_fetcher import HttpxFetcher

audit_app = typer.Typer(
    help="Objective homepage observations, without commercial scores.", no_args_is_help=True
)
FreshnessOption = Annotated[int, typer.Option("--freshness-days", min=0, max=365)]


@contextmanager
def auditor(url: str, freshness_days: int) -> Iterator[WebsiteAuditService]:
    settings = AuditSettings(freshness_days=freshness_days)
    engine = build_engine(url)
    fetcher = HttpxFetcher(settings)
    try:
        yield WebsiteAuditService(
            lambda: SqlAlchemyUnitOfWork(engine), fetcher, BeautifulSoupHtmlAnalyzer(), settings
        )
    finally:
        fetcher.close()
        engine.dispose()


def show(result: WebsiteAuditResult) -> None:
    typer.echo(
        json.dumps(
            {
                "audit": result.audit.model_dump(mode="json"),
                "reachable": result.reachable,
                "findings": [value.model_dump(mode="json") for value in result.findings],
                "evidence_created": result.evidence_created,
                "errors": result.errors,
                "warnings": result.warnings,
                "skipped_fresh": result.skipped_fresh,
            }
        )
    )


@audit_app.command("website")
def website(
    company_id: Annotated[UUID | None, typer.Option("--company-id")] = None,
    domain: Annotated[str | None, typer.Option("--domain")] = None,
    force: bool = False,
    freshness_days: FreshnessOption = 7,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    """Audit exactly one stored company; a fresh successful audit is reused unless forced."""
    with admin_errors():
        if (company_id is None) == (domain is None):
            raise ValueError("Specify exactly one of --company-id or --domain")
        if domain is not None:
            with transaction(database_url) as uow:
                company = uow.repository.find_company_identity(f"domain:{normalize_domain(domain)}")
                if company is None:
                    raise ValueError("No stored company matches this domain")
                company_id = company.id
        assert company_id is not None
        with auditor(database_url, freshness_days) as service:
            result = service.audit_company(company_id, force=force)
        show(result)
        if result.audit.status == AuditStatus.FAILED:
            raise typer.Exit(code=1)
        if result.audit.status == AuditStatus.PARTIAL:
            raise typer.Exit(code=2)


@audit_app.command("campaign")
def campaign(
    campaign: Annotated[str, typer.Option("--campaign")],
    limit: Annotated[int, typer.Option(min=1, max=100)] = 5,
    force: bool = False,
    freshness_days: FreshnessOption = 7,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    """Audit eligible companies sequentially, skipping fresh audits and missing website records."""
    with admin_errors():
        campaign_id = resolve_campaign(campaign, database_url)
        with auditor(database_url, freshness_days) as service:
            result = service.audit_campaign(campaign_id, limit=limit, force=force)
        for item in result.results:
            show(item)
        typer.echo(
            f"Audits: {sum(not value.skipped_fresh for value in result.results)}; "
            f"fresh skipped: {sum(value.skipped_fresh for value in result.results)}; "
            f"no website skipped: {result.skipped_no_website}; errors: {len(result.errors)}"
        )
        if result.errors or any(item.audit.status == AuditStatus.FAILED for item in result.results):
            raise typer.Exit(code=1)
        if any(item.audit.status == AuditStatus.PARTIAL for item in result.results):
            raise typer.Exit(code=2)


@audit_app.command("list")
def audit_list(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    with transaction(database_url) as uow:
        for audit in sorted(uow.repository.list(WebsiteAudit), key=lambda value: value.started_at):
            typer.echo(audit.model_dump_json())
