"""Small application use case with no SQLAlchemy dependency."""

from dataclasses import dataclass

from lead_engine.application.ports import DatabaseProbe


@dataclass(frozen=True)
class HealthResult:
    healthy: bool
    detail: str


def check_health(database: DatabaseProbe) -> HealthResult:
    """Translate probe failure into a safe diagnostic, without exposing credentials."""
    try:
        database.ping()
    except Exception:
        return HealthResult(healthy=False, detail="Database connectivity check failed")
    return HealthResult(healthy=True, detail="Database connectivity check passed")
