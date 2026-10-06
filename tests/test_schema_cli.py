import json
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect
from typer.testing import CliRunner

from lead_engine.cli.main import app
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.orm import Base
from lead_engine.infrastructure.schema import (
    database_revision,
    local_database_path,
    migration_config,
    reset_local_database,
    upgrade_database,
)


def test_migration_empty_database_and_idempotent_upgrade(database_url: str) -> None:
    upgrade_database(database_url)
    upgrade_database(database_url)
    engine = build_engine(database_url)
    try:
        assert database_revision(engine) == "0007"
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        command.downgrade(migration_config(database_url), "base")
        assert inspect(engine).get_table_names() == ["alembic_version"]
        upgrade_database(database_url)
        assert database_revision(engine) == "0007"
    finally:
        engine.dispose()


def test_cli_database_init_status_and_manual_records(database_url: str) -> None:
    runner = CliRunner()
    options = ["--database-url", database_url]
    absent = runner.invoke(app, ["db", "status", *options])
    # Tests use absolute tmp databases; status supports these read-only paths.
    assert absent.exit_code == 1
    assert runner.invoke(app, ["db", "init", *options]).exit_code == 0
    status = runner.invoke(app, ["db", "status", *options])
    assert status.exit_code == 0, status.output
    assert "0007" in status.stdout
    add = runner.invoke(
        app, ["company", "add", "--name", "Clinic", "--primary-domain", "clinic.pe", *options]
    )
    assert add.exit_code == 0, add.output
    company = json.loads(add.stdout)
    again = runner.invoke(
        app, ["company", "add", "--name", "CLINIC", "--primary-domain", "www.clinic.pe", *options]
    )
    assert again.exit_code == 0
    assert json.loads(again.stdout)["id"] == company["id"]
    listing = runner.invoke(app, ["company", "list", *options])
    assert listing.exit_code == 0 and len(listing.stdout.splitlines()) == 1
    campaign = runner.invoke(
        app, ["campaign", "add", "--name", "Health", "--type", "HEALTH", *options]
    )
    assert campaign.exit_code == 0, campaign.output
    assert runner.invoke(app, ["campaign", "list", *options]).exit_code == 0


def test_reset_confirmation_and_reinitialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    url = "sqlite+pysqlite:///development.db"
    runner = CliRunner()
    options = ["--database-url", url]
    assert runner.invoke(app, ["db", "init", *options]).exit_code == 0
    assert runner.invoke(app, ["company", "add", "--name", "Clinic", *options]).exit_code == 0
    assert runner.invoke(app, ["db", "reset", *options], input="n\n").exit_code != 0
    assert "Clinic" in runner.invoke(app, ["company", "list", *options]).stdout
    reset = runner.invoke(app, ["db", "reset", *options], input="y\n")
    assert reset.exit_code == 0, reset.output
    assert runner.invoke(app, ["company", "list", *options]).stdout == ""


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost/test",
        "sqlite:///:memory:",
        "sqlite:///../outside.db",
        "sqlite:///README.md",
        "sqlite:///test.db?mode=rw",
    ],
)
def test_unsafe_reset_targets_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        local_database_path(url)


def test_reset_refuses_non_database_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "important.db"
    path.write_text("valuable data")
    with pytest.raises(ValueError):
        reset_local_database("sqlite:///important.db")
    assert path.read_text() == "valuable data"


def test_cli_invalid_input_is_clean_error(database_url: str, engine: Engine) -> None:
    result = CliRunner().invoke(
        app, ["company", "add", "--name", " ", "--database-url", database_url]
    )
    assert result.exit_code == 1
    assert "ERROR" in result.output
