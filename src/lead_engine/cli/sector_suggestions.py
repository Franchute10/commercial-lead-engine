"""Human-reviewed sector suggestion commands."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.sector_suggestions import SectorSuggestionService
from lead_engine.cli.common import DatabaseOption, admin_errors
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork

sector_suggestion_app = typer.Typer(
    help="Submit and human-review business sector suggestions.", no_args_is_help=True
)


@contextmanager
def service(url: str) -> Iterator[tuple[SectorSuggestionService, SqlAlchemyUnitOfWork]]:
    with admin_errors():
        engine = build_engine(url)
        try:
            with SqlAlchemyUnitOfWork(engine) as uow:
                yield SectorSuggestionService(uow), uow
        finally:
            engine.dispose()


@sector_suggestion_app.command("submit")
def submit(
    name: Annotated[str, typer.Option("--name")],
    sector: Annotated[str | None, typer.Option("--sector")] = None,
    subsector: Annotated[str | None, typer.Option("--subsector")] = None,
    profile: Annotated[str | None, typer.Option("--profile")] = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as (suggestions, uow):
        record = suggestions.submit(name, sector, subsector, profile)
        uow.commit()
        typer.echo(record.model_dump_json(indent=2))


@sector_suggestion_app.command("list")
def list_suggestions(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    with service(database_url) as (suggestions, _):
        for record in suggestions.list():
            typer.echo(record.model_dump_json())


@sector_suggestion_app.command("show")
def show(
    suggestion_id: Annotated[UUID, typer.Option("--id")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as (suggestions, _):
        typer.echo(suggestions.get(suggestion_id).model_dump_json(indent=2))


@sector_suggestion_app.command("review")
def review(
    suggestion_id: Annotated[UUID, typer.Option("--id")],
    sector: Annotated[str | None, typer.Option("--sector")] = None,
    subsector: Annotated[str | None, typer.Option("--subsector")] = None,
    profile: Annotated[str | None, typer.Option("--profile")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as (suggestions, uow):
        record = suggestions.mark_needs_review(suggestion_id, sector, subsector, profile, note)
        uow.commit()
        typer.echo(record.model_dump_json(indent=2))


@sector_suggestion_app.command("accept")
def accept(
    suggestion_id: Annotated[UUID, typer.Option("--id")],
    sector: Annotated[str | None, typer.Option("--sector")] = None,
    subsector: Annotated[str | None, typer.Option("--subsector")] = None,
    profile: Annotated[str | None, typer.Option("--profile")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as (suggestions, uow):
        record = suggestions.accept(suggestion_id, sector, subsector, profile, note)
        uow.commit()
        typer.echo(record.model_dump_json(indent=2))


@sector_suggestion_app.command("reject")
def reject(
    suggestion_id: Annotated[UUID, typer.Option("--id")],
    note: Annotated[str, typer.Option("--note")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as (suggestions, uow):
        record = suggestions.reject(suggestion_id, note)
        uow.commit()
        typer.echo(record.model_dump_json(indent=2))
