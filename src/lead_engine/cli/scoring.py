"""Manual commercial observations and transparent scoring administration."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated
from uuid import UUID

import typer
from pydantic import JsonValue

from lead_engine.application.scoring import CommercialScoringService
from lead_engine.application.services import LeadService
from lead_engine.cli.common import DatabaseOption, admin_errors, transaction
from lead_engine.cli.scout import resolve_campaign
from lead_engine.domain.commercial import BusinessSignal
from lead_engine.domain.enums import SourceType
from lead_engine.domain.models import Evidence, Source, utc_now
from lead_engine.domain.scoring import ScoreBand, ScoringResult
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork

score_app = typer.Typer(help="Versioned evidence-based commercial scoring.", no_args_is_help=True)
evidence_app = typer.Typer(help="Manual structured commercial observations.", no_args_is_help=True)


class ValueType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    TEXT = "text"
    JSON = "json"


def parse_value(value: str, value_type: ValueType) -> JsonValue:
    if value_type == ValueType.INTEGER:
        return int(value)
    if value_type == ValueType.DECIMAL:
        try:
            number = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("Invalid decimal") from error
        if not number.is_finite():
            raise ValueError("Decimal must be finite")
        return str(number)
    if value_type == ValueType.BOOLEAN:
        if value.lower() not in {"true", "false"}:
            raise ValueError("Boolean value must be true or false")
        return value.lower() == "true"
    if value_type == ValueType.JSON:
        from pydantic import TypeAdapter

        return TypeAdapter(JsonValue).validate_json(value)
    return value


@evidence_app.command("add")
def evidence_add(
    company_id: Annotated[UUID, typer.Option("--company-id")],
    signal: Annotated[str, typer.Option("--type")],
    value: Annotated[str, typer.Option("--value")],
    value_type: Annotated[ValueType, typer.Option("--value-type")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    source: str = "manual",
    source_url: str | None = None,
    statement: str | None = None,
    confidence: float = 0.8,
    observed_at: datetime | None = None,
) -> None:
    """Attach a typed observation; source may be manual or an existing Source UUID."""
    with admin_errors():
        raw = parse_value(value, value_type)
        manual_website = {
            "NO_WEBSITE",
            "MOBILE_VIEWPORT_PRESENT",
            "META_DESCRIPTION_PRESENT",
            "CONTACT_FORM_PRESENT",
            "WHATSAPP_LINK_PRESENT",
            "HAS_RESERVATION_PATH",
            "BOOKING_CTA_PRESENT",
            "HAS_QUOTE_PATH",
            "HAS_PRODUCT_DISCOVERY_PATH",
            "CATALOG_PRESENT",
            "HAS_DIRECT_CONTACT_PATH",
            "MAP_LINK_PRESENT",
            "BUSINESS_HOURS_PRESENT",
            "HTTPS_MISSING",
            "SLOW_RESPONSE",
        }
        if signal not in BusinessSignal.__members__ and signal not in manual_website:
            raise ValueError("Unsupported commercial/manual website signal")
        if signal in manual_website and type(raw) is not bool:
            raise ValueError("Manual website observations require explicit booleans")
        with transaction(database_url) as uow:
            writer = LeadService(uow)
            if source == "manual":
                provenance = writer.add_source(
                    Source(
                        source_type=SourceType.MANUAL,
                        url=source_url,
                        title="Manual commercial observation",
                        metadata={"signal": signal},
                    )
                )
            else:
                if source_url:
                    raise ValueError("Existing source must not be overridden with --source-url")
                existing_source = uow.repository.get(Source, UUID(source))
                if existing_source is None:
                    raise ValueError("Source does not exist")
                provenance = existing_source
            evidence = writer.add_evidence(
                Evidence(
                    company_id=company_id,
                    source_id=provenance.id,
                    evidence_type=signal,
                    raw_value=raw,
                    confidence=confidence,
                    observed_at=observed_at or utc_now(),
                    statement=statement or f"Manual observation: {signal}={str(raw)[:200]}",
                )
            )
            uow.commit()
            typer.echo(evidence.model_dump_json())


@contextmanager
def scoring(url: str) -> Iterator[CommercialScoringService]:
    engine = build_engine(url)
    try:
        yield CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine))
    finally:
        engine.dispose()


def show(result: ScoringResult) -> None:
    typer.echo(f"Company: {result.company_name}")
    typer.echo(f"Campaign: {result.campaign_name}")
    typer.echo(f"Total: {result.score.total_score}/100")
    typer.echo(f"Band: {result.summary.band.value}")
    completeness = result.summary.completeness_percent
    typer.echo(
        f"Data completeness: {str(completeness) + '%' if completeness is not None else 'UNKNOWN'}"
    )
    typer.echo(f"Version: {result.score.scoring_version}")
    typer.echo(f"Score ID: {result.score.id}")
    for component in result.score.components:
        typer.echo(f"{component.criterion}: {component.points_awarded}/{component.max_points}")
        typer.echo(f"  {component.explanation}")
        refs = ", ".join(str(value) for value in component.evidence_ids) or "none (unknown)"
        typer.echo(f"  Evidence: {refs}")


@score_app.command("lead")
def score_lead(
    lead_id: Annotated[UUID, typer.Option("--lead-id")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with admin_errors(), scoring(database_url) as service:
        show(service.score_lead(lead_id))


@score_app.command("campaign")
def score_campaign(
    campaign: Annotated[str, typer.Option("--campaign")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    min_score: Annotated[int, typer.Option(min=0, max=100)] = 0,
) -> None:
    with admin_errors(), scoring(database_url) as service:
        results = service.score_campaign(resolve_campaign(campaign, database_url))
        for result in sorted(results, key=lambda item: -item.score.total_score):
            if result.score.total_score >= min_score:
                show(result)
        typer.echo(f"Recorded scores: {len(results)}; min-score is an output filter")


@score_app.command("list")
def score_list(
    campaign: Annotated[str, typer.Option("--campaign")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    band: ScoreBand | None = None,
) -> None:
    with admin_errors(), scoring(database_url) as service:
        for result in service.latest_campaign(resolve_campaign(campaign, database_url)):
            if band is None or result.summary.band == band:
                show(result)


@score_app.command("explain")
def score_explain(
    lead_id: Annotated[UUID, typer.Option("--lead-id")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with admin_errors(), scoring(database_url) as service:
        history = service.history(lead_id)
        if not history:
            raise ValueError("Lead has no score history")
        show(history[-1])


@score_app.command("history")
def score_history(
    lead_id: Annotated[UUID, typer.Option("--lead-id")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with admin_errors(), scoring(database_url) as service:
        for result in service.history(lead_id):
            typer.echo(
                json.dumps(
                    {
                        "score": result.score.model_dump(mode="json"),
                        "summary": result.summary.model_dump(mode="json"),
                    }
                )
            )
