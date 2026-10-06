"""Human-entered contacts and bounded public research commands."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

import typer

from lead_engine.application.contact_research import DecisionMakerResearchService
from lead_engine.cli.common import DatabaseOption, admin_errors, transaction
from lead_engine.cli.scout import resolve_campaign
from lead_engine.domain.contact_research import ContactCandidate, DecisionMakerResearchRun
from lead_engine.domain.enums import RoleCategory, SourceType
from lead_engine.domain.models import Contact, Lead
from lead_engine.infrastructure.contact_providers import (
    CompanyWebsiteContactProvider,
    ManualContactProvider,
)
from lead_engine.infrastructure.database import DEFAULT_DATABASE_URL, build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.website.http_fetcher import HttpxFetcher

contact_app = typer.Typer(help="Attributable public contact intelligence; human outreach only.")


@contextmanager
def service(url: str, freshness_days: int = 30) -> Iterator[DecisionMakerResearchService]:
    with admin_errors():
        engine = build_engine(url)
        try:
            yield DecisionMakerResearchService(lambda: SqlAlchemyUnitOfWork(engine), freshness_days)
        finally:
            engine.dispose()


@contact_app.command("add")
def add_contact(
    company_id: Annotated[UUID, typer.Option()],
    name: Annotated[str, typer.Option()],
    role: Annotated[str, typer.Option()],
    source_url: Annotated[str, typer.Option()],
    association_statement: Annotated[
        str, typer.Option(help="Quote/evidence explicitly tying person, role and company")
    ],
    role_category: RoleCategory = RoleCategory.UNKNOWN,
    source_type: SourceType = SourceType.MANUAL,
    linkedin_url: str | None = None,
    public_profile_url: str | None = None,
    public_email: str | None = None,
    public_phone: str | None = None,
    confidence: float = 0.7,
    context: str | None = None,
    context_statement: str | None = None,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with service(database_url) as research:
        candidate = ContactCandidate(
            full_name=name,
            role_title=role,
            role_category=role_category,
            company_id=company_id,
            source_url=source_url,
            source_type=source_type,
            association_statement=association_statement,
            linkedin_url=linkedin_url,
            public_profile_url=public_profile_url,
            public_email=public_email,
            public_phone=public_phone,
            confidence=confidence,
            context=context,
            context_statement=context_statement,
        )
        result = research.research(company_id, ManualContactProvider([candidate]), force=True)
        typer.echo(result.model_dump_json(indent=2))


@contact_app.command("list")
def list_contacts(
    company_id: Annotated[UUID, typer.Option()], database_url: DatabaseOption = DEFAULT_DATABASE_URL
) -> None:
    with transaction(database_url) as uow:
        for contact in uow.repository.list(Contact, company_id=company_id):
            typer.echo(contact.model_dump_json())


@contact_app.command("runs")
def list_runs(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    with transaction(database_url) as uow:
        for run in uow.repository.list(DecisionMakerResearchRun):
            typer.echo(run.model_dump_json())


@contact_app.command("research")
def research_contacts(
    lead_id: Annotated[UUID, typer.Option()],
    force: bool = False,
    freshness_days: int = 30,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with transaction(database_url) as uow:
        lead = uow.repository.get(Lead, lead_id)
        if lead is None:
            raise ValueError("Lead not found")
    with service(database_url, freshness_days) as research:
        fetcher = HttpxFetcher(same_domain_only=True)
        try:
            result = research.research(
                lead.company_id, CompanyWebsiteContactProvider(fetcher), lead.id, force
            )
        finally:
            fetcher.close()
        typer.echo(result.model_dump_json(indent=2))


@contact_app.command("research-campaign")
def research_campaign(
    campaign: Annotated[str, typer.Option()],
    min_lead_score: int | None = None,
    limit: int = 5,
    force: bool = False,
    freshness_days: int = 30,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with admin_errors():
        campaign_id = resolve_campaign(campaign, database_url)
    with service(database_url, freshness_days) as research:
        fetcher = HttpxFetcher(same_domain_only=True)
        try:
            results = research.research_campaign(
                campaign_id, CompanyWebsiteContactProvider(fetcher), min_lead_score, limit, force
            )
        finally:
            fetcher.close()
        for result in results:
            typer.echo(result.model_dump_json())


@contact_app.command("recommend")
def recommend(
    lead_id: UUID | None = None,
    campaign: str | None = None,
    limit: int = 3,
    freshness_days: int = 30,
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
) -> None:
    with admin_errors():
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be 1–100")
        if (lead_id is None) == (campaign is None):
            raise ValueError("Supply exactly one of --lead-id or --campaign")
        ids = [lead_id] if lead_id else []
        if campaign:
            campaign_id = resolve_campaign(campaign, database_url)
            with transaction(database_url) as uow:
                ids = [
                    lead.id for lead in uow.repository.list(Lead, campaign_id=campaign_id)[:limit]
                ]
        with service(database_url, freshness_days) as research:
            for identity in ids:
                if identity:
                    typer.echo(
                        research.recommend_contacts(identity, limit).model_dump_json(indent=2)
                    )
