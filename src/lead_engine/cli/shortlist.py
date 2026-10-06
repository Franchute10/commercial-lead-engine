"""Daily ranking, explanations, human suppressions and local exports."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.shortlist import DailyShortlistService
from lead_engine.application.shortlist_export import ShortlistFormat, export_runs, render_decision
from lead_engine.cli.common import DatabaseOption, admin_errors
from lead_engine.cli.scout import resolve_campaign
from lead_engine.domain.enums import CampaignType, LeadStatus
from lead_engine.domain.research import OpportunityType
from lead_engine.domain.shortlist import ShortlistFilters, ShortlistSettings
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork

shortlist_app = typer.Typer(
    help="Human-reviewable daily lead priorities; no outreach execution.", no_args_is_help=True
)
ConfigOption = Annotated[
    Path | None, typer.Option("--config", help="Local JSON ShortlistSettings file")
]


@contextmanager
def service(url: str, config: Path | None = None) -> Iterator[DailyShortlistService]:
    with admin_errors():
        settings = (
            ShortlistSettings.model_validate_json(config.read_text(encoding="utf-8"))
            if config
            else ShortlistSettings()
        )
        engine = build_engine(url)
        try:
            yield DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine), settings)
        finally:
            engine.dispose()


@shortlist_app.command("today")
def today(
    campaign: str | None = None,
    campaign_type: Annotated[CampaignType | None, typer.Option("--type")] = None,
    city: str | None = None,
    min_score: int | None = None,
    min_research_completeness: int = 0,
    pipeline_status: LeadStatus | None = None,
    opportunity: OpportunityType | None = None,
    limit: int = 5,
    format: ShortlistFormat = ShortlistFormat.CONSOLE,
    output: Path | None = None,
    config: ConfigOption = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with admin_errors():
        campaign_id = resolve_campaign(campaign, database_url) if campaign else None
        filters = ShortlistFilters(
            campaign_id=campaign_id,
            campaign_type=campaign_type,
            city=city,
            minimum_lead_score=min_score,
            minimum_research_completeness=min_research_completeness,
            pipeline_status=pipeline_status,
            opportunity_type=opportunity,
            limit=limit,
        )
        if output and output.exists():
            raise ValueError("Output exists; choose a new file")
        with service(database_url, config) as ranking:
            run = ranking.today(filters)
        text = export_runs([run], format)
        if output:
            with output.open("x", encoding="utf-8", newline="") as handle:
                handle.write(text)
            typer.echo(f"Saved shortlist {run.id} to {output}")
        else:
            typer.echo(text, nl=False)


@shortlist_app.command("explain")
def explain(
    lead_id: Annotated[UUID, typer.Option()],
    format: ShortlistFormat = ShortlistFormat.CONSOLE,
    config: ConfigOption = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url, config) as ranking:
        decision = ranking.explain(lead_id)
        typer.echo(
            decision.model_dump_json(indent=2)
            if format == ShortlistFormat.JSON
            else render_decision(decision),
            nl=False,
        )


@shortlist_app.command("history")
def history(
    limit: int = 10,
    format: ShortlistFormat = ShortlistFormat.CONSOLE,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as ranking:
        typer.echo(export_runs(ranking.history(limit), format), nl=False)


@shortlist_app.command("suppress")
def suppress(
    lead_id: Annotated[UUID, typer.Option()],
    days: Annotated[int, typer.Option()],
    reason: Annotated[str, typer.Option()],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as ranking:
        typer.echo(ranking.suppress(lead_id, days, reason).model_dump_json(indent=2))


@shortlist_app.command("unsuppress")
def unsuppress(
    lead_id: Annotated[UUID, typer.Option()], database_url: DatabaseOption = DEFAULT_DATABASE_URL
) -> None:
    with service(database_url) as ranking:
        typer.echo(f"Revoked {ranking.unsuppress(lead_id)} active suppression(s)")
