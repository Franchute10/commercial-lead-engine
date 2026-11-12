"""Capture explicit service requests from public sources; no crawling or outreach."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.intent_discovery import IntentDiscoveryService
from lead_engine.application.intent_signals import IntentSignalService
from lead_engine.cli.common import DatabaseOption, admin_errors, transaction
from lead_engine.domain.intent_signal import IntentSignal, RequestType, SignalPlatform
from lead_engine.domain.models import Campaign
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL
from lead_engine.infrastructure.public_search import BraveSearchProvider

intent_app = typer.Typer(help="Capture and rank explicit service requests.", no_args_is_help=True)


def _campaign_id(value: str, database_url: str) -> UUID:
    try:
        identifier = UUID(value)
    except ValueError:
        with transaction(database_url) as uow:
            campaigns = uow.repository.list(Campaign, name=value)
            if len(campaigns) != 1:
                raise ValueError("Use an existing campaign UUID or an exact unique name") from None
            return campaigns[0].id
    with transaction(database_url) as uow:
        if uow.repository.get(Campaign, identifier) is None:
            raise ValueError("Campaign does not exist")
    return identifier


@intent_app.command("capture")
def capture(
    campaign: Annotated[str, typer.Option("--campaign")],
    platform: Annotated[SignalPlatform, typer.Option("--platform")],
    request_type: Annotated[RequestType, typer.Option("--request-type")],
    source_url: Annotated[str, typer.Option("--url")],
    service_requested: Annotated[str, typer.Option("--service")],
    evidence_excerpt: Annotated[str, typer.Option("--excerpt")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    city: str | None = None,
    region: str | None = None,
    country: str | None = None,
    posted_at: Annotated[str | None, typer.Option(help="ISO-8601 timestamp from the post")] = None,
) -> None:
    """Save one public post; use only content you are allowed to access and retain."""
    with admin_errors():
        parsed_posted_at = None
        if posted_at:
            parsed_posted_at = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
            if parsed_posted_at.tzinfo is None:
                raise ValueError("posted_at must include a timezone, for example -04:00 or Z")
        campaign_id = _campaign_id(campaign, database_url)
        with transaction(database_url) as uow:
            record = IntentSignalService(uow).capture(
                campaign_id=campaign_id,
                platform=platform,
                source_url=source_url,
                request_type=request_type,
                service_requested=service_requested,
                evidence_excerpt=evidence_excerpt,
                city=city,
                region=region,
                country=country,
                posted_at=parsed_posted_at,
            )
            uow.commit()
            typer.echo(record.model_dump_json())



@intent_app.command("discover")
def discover(
    campaign: Annotated[str, typer.Option("--campaign")],
    service: Annotated[str, typer.Option("--service", help="Service phrase, for example house cleaning")],
    country: Annotated[str, typer.Option("--country", help="Two-letter search country code")] = "US",
    language: Annotated[str, typer.Option("--language", help="Search language, for example en or es")] = "en",
    freshness: Annotated[str, typer.Option("--freshness", help="Brave freshness filter: pd, pw, pm, or py")] = "pm",
    per_query: Annotated[int, typer.Option("--per-query", min=1, max=20)] = 10,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    """Search indexed public discussions and save qualified service-request candidates."""
    with admin_errors():
        campaign_id = _campaign_id(campaign, database_url)
        with transaction(database_url) as uow:
            records, ignored, query_count = IntentDiscoveryService(
                uow, BraveSearchProvider()
            ).discover(
                campaign_id=campaign_id,
                service=service,
                country_code=country,
                search_lang=language,
                count=per_query,
                freshness=freshness,
            )
            uow.commit()
            typer.echo(
                f"Searches: {query_count}; captured: {len(records)}; "
                f"skipped or duplicate: {ignored}"
            )
            for record in records:
                typer.echo(record.model_dump_json())


@intent_app.command("list")
def list_signals(
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    campaign: Annotated[str | None, typer.Option("--campaign")] = None,
) -> None:
    """List captured public requests ranked by evidence-backed fit score."""
    with admin_errors():
        campaign_id = _campaign_id(campaign, database_url) if campaign else None
        with transaction(database_url) as uow:
            for record in IntentSignalService(uow).list_for_campaign(campaign_id):
                typer.echo(record.model_dump_json())
