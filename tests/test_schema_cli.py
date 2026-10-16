import json
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect
from typer.testing import CliRunner

from lead_engine.application.discovery import discovery_query_for_campaign
from lead_engine.cli.main import app
from lead_engine.domain.models import Campaign
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
        assert database_revision(engine) == "0012"
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        command.downgrade(migration_config(database_url), "base")
        assert inspect(engine).get_table_names() == ["alembic_version"]
        upgrade_database(database_url)
        assert database_revision(engine) == "0012"
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
    assert "0012" in status.stdout
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
    campaign_record = json.loads(campaign.stdout)
    assert campaign_record["profile_id"] == "health-v1"
    assert campaign_record["sector_id"] is None
    assert campaign_record["subsector_id"] is None
    assert runner.invoke(app, ["campaign", "list", *options]).exit_code == 0


def test_campaign_cli_sector_persistence_and_invalid_sectors_do_not_write(
    database_url: str,
) -> None:
    runner = CliRunner()
    options = ["--database-url", database_url]
    assert runner.invoke(app, ["db", "init", *options]).exit_code == 0

    valid = runner.invoke(
        app,
        [
            "campaign",
            "add",
            "--name",
            "Clinics Chiclayo",
            "--profile",
            "health-v1",
            "--sector",
            "health",
            "--subsector",
            "private-clinics",
            "--industry",
            "healthcare",
            "--subindustry",
            "outpatient clinics",
            *options,
        ],
    )
    assert valid.exit_code == 0, valid.output
    valid_record = json.loads(valid.stdout)
    assert valid_record["sector_id"] == "health"
    assert valid_record["subsector_id"] == "private-clinics"
    assert valid_record["industry"] == "healthcare"
    assert valid_record["subindustry"] == "outpatient clinics"

    for invalid_args in (
        ["--name", "Logistics Lima", "--profile", "health-v1", "--sector", "logistics-transport"],
        ["--name", "Mismatched", "--type", "CONSTRUCTION", "--sector", "health"],
        ["--name", "Bad subsector", "--type", "HEALTH", "--subsector", "private-clinics"],
        ["--name", "Bad pair", "--type", "HEALTH", "--sector", "health", "--subsector", "hotels"],
    ):
        failed = runner.invoke(app, ["campaign", "add", *invalid_args, *options])
        assert failed.exit_code != 0

    listed = runner.invoke(app, ["campaign", "list", *options])
    assert listed.exit_code == 0, listed.output
    records = [json.loads(line) for line in listed.stdout.splitlines()]
    assert [item["name"] for item in records] == ["Clinics Chiclayo"]
    assert records[0]["sector_id"] == "health"
    assert records[0]["subsector_id"] == "private-clinics"


def test_campaign_cli_profiles_structured_targeting_and_profile_resolution(
    database_url: str,
) -> None:
    runner = CliRunner()
    options = ["--database-url", database_url]
    assert runner.invoke(app, ["db", "init", *options]).exit_code == 0

    profiles = runner.invoke(app, ["campaign", "profiles"])
    assert profiles.exit_code == 0, profiles.output
    profile_values = [json.loads(line) for line in profiles.stdout.splitlines()]
    assert [profile["profile_id"] for profile in profile_values] == [
        "health-v1",
        "construction-v1",
        "hospitality-v1",
    ]
    assert all(
        "scoring_profile" in profile and "target_roles" in profile for profile in profile_values
    )

    legacy = runner.invoke(
        app,
        ["campaign", "add", "--name", "Legacy", "--type", "HEALTH", *options],
    )
    assert legacy.exit_code == 0, legacy.output
    assert json.loads(legacy.stdout)["profile_id"] == "health-v1"

    explicit = runner.invoke(
        app,
        [
            "campaign",
            "add",
            "--name",
            "Chiclayo health",
            "--profile",
            "health-v1",
            "--country",
            "Peru",
            "--region",
            "Lambayeque",
            "--city",
            "Chiclayo",
            "--industry",
            "health",
            "--subindustry",
            "private clinics",
            "--geography",
            "Northern Peru",
            *options,
        ],
    )
    assert explicit.exit_code == 0, explicit.output
    campaign_data = json.loads(explicit.stdout)
    assert campaign_data["campaign_type"] == "HEALTH"
    assert campaign_data["profile_id"] == "health-v1"
    assert campaign_data["country"] == "Peru"
    assert campaign_data["region"] == "Lambayeque"
    assert campaign_data["city"] == "Chiclayo"
    assert campaign_data["industry"] == "health"
    assert campaign_data["subindustry"] == "private clinics"
    assert campaign_data["geography"] == "Northern Peru"

    matching = runner.invoke(
        app,
        [
            "campaign",
            "add",
            "--name",
            "Matching",
            "--profile",
            "construction-v1",
            "--type",
            "CONSTRUCTION",
            *options,
        ],
    )
    assert matching.exit_code == 0, matching.output

    for invalid_args in (
        ["--name", "Mismatch", "--profile", "health-v1", "--type", "CONSTRUCTION"],
        ["--name", "Unknown", "--profile", "unknown-v1"],
        ["--name", "Missing"],
    ):
        failed = runner.invoke(app, ["campaign", "add", *invalid_args, *options])
        assert failed.exit_code != 0

    listing = runner.invoke(app, ["campaign", "list", *options])
    assert listing.exit_code == 0, listing.output
    campaigns = [json.loads(line) for line in listing.stdout.splitlines()]
    assert len(campaigns) == 3
    persisted = next(item for item in campaigns if item["name"] == "Chiclayo health")
    campaign = Campaign.model_validate(persisted)
    query = discovery_query_for_campaign(campaign)
    assert (query.country, query.region, query.city) == ("Peru", "Lambayeque", "Chiclayo")
    assert query.industry == "health"


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
