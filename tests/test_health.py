import pytest
from typer.testing import CliRunner

from lead_engine.application.health import HealthResult, check_health
from lead_engine.cli.main import app
from lead_engine.infrastructure.database import SqlAlchemyDatabaseProbe, build_engine


class BrokenProbe:
    def ping(self) -> None:
        raise RuntimeError("secret connection information")


def test_database_health() -> None:
    engine = build_engine()
    try:
        assert check_health(SqlAlchemyDatabaseProbe(engine)).healthy
    finally:
        engine.dispose()


def test_failed_probe_hides_sensitive_details() -> None:
    result = check_health(BrokenProbe())
    assert not result.healthy
    assert "secret" not in result.detail


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "health" in result.stdout


def test_cli_health() -> None:
    result = CliRunner().invoke(app, ["health"])
    assert result.exit_code == 0
    assert "OK" in result.stdout


def test_cli_failure_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_check(database: object) -> HealthResult:
        return HealthResult(healthy=False, detail="Database connectivity check failed")

    monkeypatch.setattr("lead_engine.cli.main.check_health", failed_check)
    result = CliRunner().invoke(app, ["health"])
    assert result.exit_code == 1
    assert "ERROR" in result.stdout
