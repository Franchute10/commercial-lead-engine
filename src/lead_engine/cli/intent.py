"""Capture explicit service requests from public sources; no crawling or outreach."""

import json
from dataclasses import asdict
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
from lead_engine.infrastructure.public_search import TavilySearchProvider
from lead_engine.infrastructure.website.http_fetcher import HttpxFetcher
from lead_engine.infrastructure.website.search_terms import (
    WebsiteSearchProfile,
    extract_website_search_profile,
)

intent_app = typer.Typer(help="Capture and rank explicit service requests.", no_args_is_help=True)


def _website_profile(website: str) -> WebsiteSearchProfile:
    fetcher = HttpxFetcher()
    try:
        result = fetcher.fetch(website)
    finally:
        fetcher.close()
    if result.error_code or result.http_status is None or not 200 <= result.http_status < 300:
        raise ValueError(
            f"Could not read the public website ({result.error_code or result.http_status})"
        )
    return extract_website_search_profile(result.final_url or website, result.body)


@intent_app.command("profile")
def profile(
    website: Annotated[str, typer.Option("--website", help="Public business website URL")],
) -> None:
    """Extract reviewable service phrases from a public website's title and headings."""
    with admin_errors():
        typer.echo(json.dumps(asdict(_website_profile(website)), ensure_ascii=False))


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
    segment: Annotated[
        str | None,
        typer.Option("--segment", help="Target buyer segment, e.g. homeowners or property managers"),
    ] = None,
    country: Annotated[str, typer.Option("--country", help="Two-letter search country code")] = "US",
    language: Annotated[str, typer.Option("--language", help="Search language, for example en or es")] = "en",
    freshness: Annotated[str, typer.Option("--freshness", help="Tavily time range: pd, pw, pm, or py")] = "pm",
    per_query: Annotated[int, typer.Option("--per-query", min=1, max=20)] = 10,
    zones: Annotated[
        list[str] | None,
        typer.Option(
            "--zone",
            help="Additional service-area city or neighborhood; repeat up to four times",
        ),
    ] = None,
    website: Annotated[
        str | None,
        typer.Option("--website", help="Public business website whose phrases expand searches"),
    ] = None,
    include_website_terms: Annotated[
        bool,
        typer.Option(
            "--include-website-terms",
            help="Use terms after reviewing them with `intent profile --website`",
        ),
    ] = False,
    show_filtered: Annotated[
        bool,
        typer.Option(
            "--show-filtered",
            help="Print up to 15 public search results discarded by the intent filters",
        ),
    ] = False,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    """Search indexed public discussions and save buyer-intent candidates for review."""
    with admin_errors():
        if include_website_terms and not website:
            raise ValueError("--include-website-terms requires --website")
        if website and not include_website_terms:
            raise ValueError(
                "Review terms first with `intent profile --website URL`, then pass "
                "--include-website-terms"
            )
        additional_terms = _website_profile(website).keyword_candidates if website else ()
        if additional_terms:
            typer.echo("Website phrases used: " + json.dumps(additional_terms, ensure_ascii=False))
        if zones:
            typer.echo("Additional search zones: " + json.dumps(zones, ensure_ascii=False))
        campaign_id = _campaign_id(campaign, database_url)
        with transaction(database_url) as uow:
            discovery = IntentDiscoveryService(uow, TavilySearchProvider())
            records, ignored, query_count = discovery.discover(
                campaign_id=campaign_id,
                service=service,
                country_code=country,
                search_lang=language,
                count=per_query,
                freshness=freshness,
                segment=segment,
                additional_terms=additional_terms,
                zones=tuple(zones or ()),
                show_filtered=show_filtered,
            )
            uow.commit()
            qualified = sum(record.qualified_candidate for record in records)
            typer.echo(
                f"Searches: {query_count}; candidates: {len(records)}; "
                f"qualified: {qualified}; review needed: {len(records) - qualified}; "
                f"filtered or duplicate: {ignored}"
            )
            typer.echo(f"Source hits: {discovery.search_lane_counts}")
            typer.echo(f"Filter reasons: {discovery.rejection_counts}")
            if show_filtered and discovery.filtered_samples:
                typer.echo(
                    "Filtered samples: "
                    + json.dumps(discovery.filtered_samples, ensure_ascii=False)
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
