"""Shared local CLI transaction and error handling."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from sqlalchemy.exc import SQLAlchemyError

from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork

DatabaseOption = Annotated[str, typer.Option("--database-url", envvar="LEAD_ENGINE_DATABASE_URL")]


@contextmanager
def admin_errors() -> Iterator[None]:
    try:
        yield
    except (ValueError, OSError, SQLAlchemyError) as error:
        detail = (
            "Database operation failed; verify schema and relationships"
            if isinstance(error, SQLAlchemyError)
            else str(error)
        )
        typer.echo(f"ERROR: {detail}", err=True)
        raise typer.Exit(code=1) from error


@contextmanager
def transaction(url: str) -> Iterator[SqlAlchemyUnitOfWork]:
    with admin_errors():
        engine = build_engine(url)
        try:
            with SqlAlchemyUnitOfWork(engine) as uow:
                yield uow
        finally:
            engine.dispose()
