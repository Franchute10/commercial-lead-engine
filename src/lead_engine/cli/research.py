"""Local research generation, snapshot reads and Markdown/JSON/CSV exports."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.research import CommercialResearchService
from lead_engine.application.research_export import BriefFormat, export_briefs
from lead_engine.cli.common import DatabaseOption, admin_errors
from lead_engine.cli.scout import resolve_campaign
from lead_engine.domain.research import CommercialBrief
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork

research_app = typer.Typer(
    help="Deterministic, evidence-backed commercial briefs.", no_args_is_help=True
)


@contextmanager
def service(url: str) -> Iterator[CommercialResearchService]:
    with admin_errors():
        engine = build_engine(url)
        try:
            yield CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
        finally:
            engine.dispose()


def output(briefs: list[CommercialBrief], format: BriefFormat, export: Path | None = None) -> None:
    if export:
        suffix = export.suffix.casefold()
        formats = {".json": BriefFormat.JSON, ".md": BriefFormat.MARKDOWN, ".csv": BriefFormat.CSV}
        if suffix not in formats:
            raise ValueError("Export suffix must be .json, .md or .csv")
        if export.exists():
            raise ValueError("Export target already exists; choose a new file")
        text = export_briefs(briefs, formats[suffix])
        with export.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
        typer.echo(f"Exported {len(briefs)} brief(s) to {export}")
    else:
        typer.echo(export_briefs(briefs, format), nl=False)


@research_app.command("lead")
def research_lead(
    lead_id: Annotated[UUID, typer.Option()],
    format: BriefFormat = BriefFormat.MARKDOWN,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as research:
        output([research.research_lead(lead_id)], format)


@research_app.command("campaign")
def research_campaign(
    campaign: Annotated[str, typer.Option()],
    min_score: int | None = None,
    limit: int = 5,
    format: BriefFormat = BriefFormat.MARKDOWN,
    export: Path | None = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with admin_errors():
        campaign_id = resolve_campaign(campaign, database_url)
        with service(database_url) as research:
            output(research.research_campaign(campaign_id, min_score, limit), format, export)


@research_app.command("show")
def show(
    lead_id: Annotated[UUID, typer.Option()],
    format: BriefFormat = BriefFormat.MARKDOWN,
    export: Path | None = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as research:
        output([research.latest(lead_id)], format, export)


@research_app.command("history")
def history(
    lead_id: Annotated[UUID, typer.Option()],
    format: BriefFormat = BriefFormat.MARKDOWN,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as research:
        output(research.history(lead_id), format)


@research_app.command("list")
def list_briefs(
    campaign: Annotated[str, typer.Option()],
    format: BriefFormat = BriefFormat.CSV,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with admin_errors():
        campaign_id = resolve_campaign(campaign, database_url)
        with service(database_url) as research:
            output(research.list_campaign(campaign_id), format)
