"""Local admin CLI and adapter composition. No external communications."""

from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.engine import make_url

from lead_engine.application.health import check_health
from lead_engine.application.services import LeadService
from lead_engine.cli.common import DatabaseOption, admin_errors, transaction
from lead_engine.cli.scout import scout_app
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.models import Campaign, Company
from lead_engine.infrastructure.database import (
    DEFAULT_DATABASE_URL,
    SqlAlchemyDatabaseProbe,
    build_engine,
)
from lead_engine.infrastructure.schema import (
    database_revision,
    latest_revision,
    local_database_path,
    reset_local_database,
    upgrade_database,
)

app = typer.Typer(help="Local-first Commercial Lead Engine.", no_args_is_help=True)
db_app = typer.Typer(help="Local database administration.", no_args_is_help=True)
company_app = typer.Typer(help="Manual company records.", no_args_is_help=True)
campaign_app = typer.Typer(help="Campaign records.", no_args_is_help=True)
app.add_typer(scout_app, name="scout")
app.add_typer(db_app, name="db")
app.add_typer(company_app, name="company")
app.add_typer(campaign_app, name="campaign")


@app.callback()
def main() -> None:
    """Evidence-based commercial research."""


@app.command()
def health() -> None:
    """Check application and ephemeral SQLite connectivity without creating files."""
    engine = build_engine()
    try:
        result = check_health(SqlAlchemyDatabaseProbe(engine))
    finally:
        engine.dispose()
    typer.echo(f"{'OK' if result.healthy else 'ERROR'}: {result.detail}")
    if not result.healthy:
        raise typer.Exit(code=1)


@db_app.command("init")
def db_init(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    """Apply all Alembic migrations (idempotent)."""
    with admin_errors():
        upgrade_database(database_url)
    typer.echo("Database initialized at migration head")


@db_app.command("status")
def db_status(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    """Report schema revision; fail for absent or outdated schema."""
    with admin_errors():
        if database_url.startswith("sqlite"):
            parsed = make_url(database_url)
            path = Path(parsed.database or "")
            if not parsed.database or parsed.database == ":memory:" or not path.exists():
                typer.echo("Database not initialized")
                raise typer.Exit(code=1)
        engine = build_engine(database_url)
        try:
            revision = database_revision(engine)
        finally:
            engine.dispose()
        typer.echo(f"revision={revision or 'uninitialized'} head={latest_revision()}")
        if revision is None or revision != latest_revision():
            raise typer.Exit(code=1)


@db_app.command("reset")
def db_reset(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    """Confirm and recreate a local development SQLite database inside the working directory."""
    with admin_errors():
        path = local_database_path(database_url)
        typer.confirm(f"Delete all data in {path}? Close other database clients first", abort=True)
        reset_local_database(database_url)
    typer.echo("Local database reset at migration head")


@company_app.command("add")
def company_add(
    name: Annotated[str, typer.Option("--name")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    website: str | None = None,
    primary_domain: str | None = None,
    legal_name: str | None = None,
    city: str | None = None,
    country: str | None = None,
    industry: str | None = None,
) -> None:
    """Create or enrich an exact matching company without overwriting known facts."""
    with transaction(database_url) as uow:
        company = LeadService(uow).upsert_company(
            Company(
                canonical_name=name,
                website=website,
                primary_domain=primary_domain,
                legal_name=legal_name,
                city=city,
                country=country,
                industry=industry,
            )
        )
        uow.commit()
        typer.echo(company.model_dump_json())


@company_app.command("list")
def company_list(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    with transaction(database_url) as uow:
        for company in uow.repository.list(Company):
            typer.echo(company.model_dump_json())


@campaign_app.command("add")
def campaign_add(
    name: Annotated[str, typer.Option("--name")],
    campaign_type: Annotated[CampaignType, typer.Option("--type")],
    database_url: DatabaseOption = DEFAULT_DATABASE_URL,
    geography: str | None = None,
) -> None:
    with transaction(database_url) as uow:
        campaign = LeadService(uow).create_campaign(
            Campaign(
                name=name,
                campaign_type=campaign_type,
                geography=geography,
            )
        )
        uow.commit()
        typer.echo(campaign.model_dump_json())


@campaign_app.command("list")
def campaign_list(database_url: DatabaseOption = DEFAULT_DATABASE_URL) -> None:
    with transaction(database_url) as uow:
        for campaign in uow.repository.list(Campaign):
            typer.echo(campaign.model_dump_json())
