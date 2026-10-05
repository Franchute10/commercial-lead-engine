from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.schema import upgrade_database


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    upgrade_database(database_url)
    value = build_engine(database_url)
    try:
        yield value
    finally:
        value.dispose()
