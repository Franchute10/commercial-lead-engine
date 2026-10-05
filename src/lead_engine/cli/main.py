"""CLI composition root: wire adapters to application use cases here."""

import typer

from lead_engine.application.health import check_health
from lead_engine.infrastructure.database import SqlAlchemyDatabaseProbe, build_engine

app = typer.Typer(help="Local-first Commercial Lead Engine.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Evidence-based commercial research foundation."""


@app.command()
def health() -> None:
    """Check application and SQLite connectivity without creating files."""
    engine = build_engine()
    try:
        result = check_health(SqlAlchemyDatabaseProbe(engine))
    finally:
        engine.dispose()
    typer.echo(f"{'OK' if result.healthy else 'ERROR'}: {result.detail}")
    if not result.healthy:
        raise typer.Exit(code=1)
