"""SQLAlchemy connection adapter; no schema or discovery is created yet."""

from sqlalchemy import Engine, create_engine, text


def build_engine(url: str = "sqlite+pysqlite:///:memory:") -> Engine:
    """Accept SQLAlchemy URLs to allow a later PostgreSQL adapter/driver."""
    return create_engine(url)


class SqlAlchemyDatabaseProbe:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def ping(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))
