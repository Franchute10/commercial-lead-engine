"""Engine construction and SQLite foreign-key enforcement."""

import sqlite3

from sqlalchemy import Engine, create_engine, event, text

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///lead_engine.db"


def build_engine(url: str = "sqlite+pysqlite:///:memory:") -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def _enable_foreign_keys(connection: sqlite3.Connection, record: object) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class SqlAlchemyDatabaseProbe:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def ping(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))
