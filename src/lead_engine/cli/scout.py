"""Discovery admin commands; provider configuration lives at the composition root."""

import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.discovery import DiscoveryProvider, DiscoveryQuery
from lead_engine.application.scout import ScoutService
from lead_engine.cli.common import DatabaseOption, admin_errors, transaction
from lead_engine.domain.discovery import DiscoveryRun, DiscoveryStatus
from lead_engine.domain.models import Campaign
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.discovery.csv_provider import CsvDiscoveryProvider
from lead_engine.infrastructure.discovery.reports import export_report
from lead_engine.infrastructure.discovery.static_provider import StaticDiscoveryProvider
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork

scout_app = typer.Typer(
    help="Company discovery and provenance; static is a local demo.", no_args_is_help=True
)
CampaignOption = Annotated[
    str, typer.Option("--campaign", help="Existing campaign UUID or exact name")
]
ReportOption = Annotated[
    Path | None, typer.Option("--report", help="New CSV report file (no overwrite)")
]


def resolve_campaign(value: str, url: str) -> UUID:
    with transaction(url) as uow:
        try:
            campaign_id = UUID(value)
        except ValueError:
            campaigns = uow.repository.list(Campaign, name=value)
            if len(campaigns) != 1:
                raise ValueError(
                    "Campaign name must identify exactly one existing campaign; use its UUID"
                ) from None
            return campaigns[0].id
        if uow.repository.get(Campaign, campaign_id) is None:
            raise ValueError("Campaign does not exist")
        return campaign_id


def execute(
    query: DiscoveryQuery, provider: DiscoveryProvider, url: str, report: Path | None
) -> None:
    if report is not None and report.exists():
        raise ValueError("Report file already exists; choose a new path")
    engine = build_engine(url)
    try:
        run = ScoutService(lambda: SqlAlchemyUnitOfWork(engine)).discover(query, provider)
    finally:
        engine.dispose()
    typer.echo(f"Run: {run.id} ({run.status.value})")
    labels = {
        "candidates": "Candidates",
        "companies_created": "Companies created",
        "companies_reused": "Companies reused",
        "conflicts": "Conflicts",
        "rejected": "Rejected",
        "errors": "Errors",
        "leads_created": "Leads created",
        "leads_reused": "Leads reused",
    }
    for key, label in labels.items():
        typer.echo(f"{label}: {run.counts[key]}")
    for outcome in run.outcomes:
        if outcome.message:
            typer.echo(
                f"Candidate {outcome.candidate_number} row={outcome.row_number}: {outcome.message}"
            )
    if run.provider_error:
        typer.echo(run.provider_error, err=True)
    if report is not None:
        export_report(run, report)
        typer.echo(f"Report: {report}")
    if run.status == DiscoveryStatus.FAILED or run.counts["errors"]:
        raise typer.Exit(code=1)
    if run.status == DiscoveryStatus.PARTIAL:
        raise typer.Exit(code=2)


@scout_app.command("import-csv")
def import_csv(
    campaign: CampaignOption,
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    report: ReportOption = None,
    country: str | None = None,
    limit: Annotated[int, typer.Option(min=1, max=10000)] = 1000,
) -> None:
    """Import UTF-8 candidates through Scout; bad rows are reported and audited."""
    with admin_errors():
        if report is not None and report.resolve() == file.resolve():
            raise ValueError("Report must not replace the input CSV")
        query = DiscoveryQuery(
            campaign_id=resolve_campaign(campaign, database_url), country=country, limit=limit
        )
        execute(query, CsvDiscoveryProvider(file), database_url, report)


@scout_app.command("run")
def scout_run(
    campaign: CampaignOption,
    fixture: Annotated[Path, typer.Option("--fixture", exists=True, dir_okay=False)],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    provider: str = "static",
    query_text: Annotated[str | None, typer.Option("--query")] = None,
    country: str | None = None,
    city: str | None = None,
    region: str | None = None,
    industry: str | None = None,
    limit: Annotated[int, typer.Option(min=1, max=10000)] = 20,
    report: ReportOption = None,
) -> None:
    """Consume saved public-search candidates; no live HTTP/search is performed."""
    with admin_errors():
        if provider != "static":
            raise ValueError("Only the fixture-backed static provider is available")
        query = DiscoveryQuery(
            campaign_id=resolve_campaign(campaign, database_url),
            country=country,
            city=city,
            region=region,
            industry=industry,
            query_text=query_text,
            limit=limit,
        )
        execute(query, StaticDiscoveryProvider.from_file(fixture), database_url, report)


@scout_app.command("runs")
def runs(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    """List persisted runs as JSON, including derived summary counts."""
    with transaction(database_url) as uow:
        for run in sorted(uow.repository.list(DiscoveryRun), key=lambda value: value.started_at):
            typer.echo(json.dumps({**run.model_dump(mode="json"), "counts": run.counts}))


@scout_app.command("report")
def report_run(
    run_id: UUID,
    file: Annotated[Path, typer.Option("--file")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    """Export any persisted run to a new CSV file."""
    with transaction(database_url) as uow:
        run = uow.repository.get(DiscoveryRun, run_id)
        if run is None:
            raise ValueError("Discovery run does not exist")
        export_report(run, file)
        typer.echo(f"Report: {file}")
