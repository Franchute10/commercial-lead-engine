"""Pilot labels and reports only; no rule tuning, scoring or outbound activity."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.pilot import PilotEvaluationService
from lead_engine.application.pilot_export import PilotFormat, export_report
from lead_engine.cli.common import DatabaseOption, admin_errors
from lead_engine.cli.scout import resolve_campaign
from lead_engine.domain.pilot import ContactDecision, DecisionMakerQuality, OutputQuality
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork

pilot_app = typer.Typer(
    help="Human evaluation of frozen shortlists; never changes scoring.", no_args_is_help=True
)


@contextmanager
def service(url: str) -> Iterator[PilotEvaluationService]:
    with admin_errors():
        engine = build_engine(url)
        try:
            yield PilotEvaluationService(lambda: SqlAlchemyUnitOfWork(engine))
        finally:
            engine.dispose()


@pilot_app.command("evaluate")
def evaluate(
    lead_id: Annotated[UUID, typer.Option()],
    would_contact: Annotated[ContactDecision, typer.Option()],
    decision_maker: Annotated[DecisionMakerQuality, typer.Option()],
    opportunity: Annotated[OutputQuality, typer.Option()],
    outreach: Annotated[OutputQuality, typer.Option()],
    notes: str | None = None,
    evaluator: str = "Frank",
    shortlist_run_id: UUID | None = None,
    draft_id: UUID | None = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as pilot:
        record = pilot.evaluate(
            lead_id,
            would_contact,
            decision_maker,
            opportunity,
            outreach,
            notes,
            evaluator,
            shortlist_run_id,
            draft_id,
        )
        typer.echo(record.model_dump_json(indent=2))


@pilot_app.command("report")
def report(
    campaign: Annotated[str, typer.Option()],
    evaluator: str = "Frank",
    shortlist_run_id: UUID | None = None,
    format: PilotFormat = PilotFormat.MARKDOWN,
    output: Path | None = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as pilot:
        if output and output.exists():
            raise ValueError("Output exists; choose a new file")
        result = pilot.report(resolve_campaign(campaign, database_url), evaluator, shortlist_run_id)
        text = export_report(result, format)
        if output:
            with output.open("x", encoding="utf-8", newline="") as handle:
                handle.write(text)
        else:
            typer.echo(text, nl=False)


@pilot_app.command("history")
def history(
    lead_id: Annotated[UUID, typer.Option()], database_url: DatabaseOption = DEFAULT_DATABASE_URL
) -> None:
    with service(database_url) as pilot:
        for record in pilot.history(lead_id):
            typer.echo(record.model_dump_json())
