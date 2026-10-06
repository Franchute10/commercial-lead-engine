"""Human review only: drafts, local exports and explicit decisions."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.outreach import OutreachDraftService
from lead_engine.application.outreach_export import OutreachFormat, export_drafts
from lead_engine.cli.common import DatabaseOption, admin_errors
from lead_engine.cli.scout import resolve_campaign
from lead_engine.domain.outreach import (
    DraftStatus,
    DraftView,
    OutreachChannel,
    OutreachLanguage,
    OutreachPurpose,
    OutreachSettings,
)
from lead_engine.domain.shortlist import ShortlistFilters, ShortlistSettings
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork

outreach_app = typer.Typer(
    help="Prepare drafts for human approval; never sends messages.", no_args_is_help=True
)
ID = Annotated[UUID, typer.Option()]
Config = Annotated[Path | None, typer.Option(help="JSON OutreachSettings file")]
ShortlistConfig = Annotated[
    Path | None, typer.Option(help="JSON ShortlistSettings file; existing cooldowns apply")
]


@contextmanager
def service(
    url: str, config: Path | None = None, shortlist_config: Path | None = None
) -> Iterator[OutreachDraftService]:
    with admin_errors():
        settings = (
            OutreachSettings.model_validate_json(config.read_text(encoding="utf-8"))
            if config
            else None
        )
        shortlist_settings = (
            ShortlistSettings.model_validate_json(shortlist_config.read_text(encoding="utf-8"))
            if shortlist_config
            else None
        )
        engine = build_engine(url)
        try:
            yield OutreachDraftService(
                lambda: SqlAlchemyUnitOfWork(engine), settings, shortlist_settings
            )
        finally:
            engine.dispose()


def emit(views: list[DraftView], format: OutreachFormat, output: Path | None) -> None:
    text = export_drafts(views, format)
    if output:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(text)
    else:
        typer.echo(text, nl=False)


@outreach_app.command("draft")
def draft(
    lead_id: ID,
    channel: OutreachChannel | None = None,
    purpose: OutreachPurpose | None = None,
    language: OutreachLanguage = OutreachLanguage.ES,
    force: bool = False,
    format: OutreachFormat = OutreachFormat.TEXT,
    output: Path | None = None,
    config: Config = None,
    shortlist_config: ShortlistConfig = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url, config, shortlist_config) as writer:
        if output and output.exists():
            raise ValueError("Output exists; choose a new file")
        emit([writer.draft(lead_id, channel, purpose, language, force)], format, output)


@outreach_app.command("shortlist")
def shortlist(
    campaign: str | None = None,
    limit: int = 5,
    language: OutreachLanguage = OutreachLanguage.ES,
    format: OutreachFormat = OutreachFormat.TEXT,
    output: Path | None = None,
    config: Config = None,
    shortlist_config: ShortlistConfig = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url, config, shortlist_config) as writer:
        if output and output.exists():
            raise ValueError("Output exists; choose a new file")
        campaign_id = resolve_campaign(campaign, database_url) if campaign else None
        views, skipped = writer.shortlist(
            ShortlistFilters(campaign_id=campaign_id, limit=limit), language
        )
        emit(views, format, output)
        for message in skipped:
            typer.echo(f"Skipped: {message}", err=True)


@outreach_app.command("list")
def list_drafts(
    status: DraftStatus | None = None,
    lead_id: UUID | None = None,
    format: OutreachFormat = OutreachFormat.TEXT,
    output: Path | None = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as writer:
        emit(writer.list_drafts(status, lead_id), format, output)


@outreach_app.command("show")
def show(
    draft_id: ID,
    format: OutreachFormat = OutreachFormat.TEXT,
    output: Path | None = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as writer:
        emit([writer.show(draft_id)], format, output)


@outreach_app.command("approve")
def approve(draft_id: ID, database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    with service(database_url) as writer:
        emit([writer.transition(draft_id, DraftStatus.APPROVED)], OutreachFormat.TEXT, None)


@outreach_app.command("reject")
def reject(
    draft_id: ID,
    reason: Annotated[str, typer.Option()],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as writer:
        emit([writer.transition(draft_id, DraftStatus.REJECTED, reason)], OutreachFormat.TEXT, None)


@outreach_app.command("mark-used")
def mark_used(
    draft_id: ID,
    record_interaction: bool = False,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    """Human attestation; --record-interaction records activity, never delivery."""
    with service(database_url) as writer:
        emit(
            [writer.transition(draft_id, DraftStatus.USED, record_interaction=record_interaction)],
            OutreachFormat.TEXT,
            None,
        )


@outreach_app.command("referral-add")
def referral_add(
    lead_id: ID,
    contact_id: Annotated[UUID, typer.Option()],
    referrer: Annotated[str, typer.Option()],
    source_url: Annotated[str, typer.Option()],
    statement: Annotated[str, typer.Option(help="Explicit human attestation of recommendation")],
    warm_introduction: bool = False,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    """Record an actual referral; never infer one from a note or acquaintance."""
    with service(database_url) as writer:
        typer.echo(
            writer.record_referral(
                lead_id, contact_id, referrer, source_url, statement, warm_introduction
            ).model_dump_json(indent=2)
        )
