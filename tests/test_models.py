from datetime import UTC, datetime

import pytest
from pydantic import HttpUrl, ValidationError

from lead_engine.domain.models import Company, Evidence


def evidence() -> Evidence:
    return Evidence(
        source_url=HttpUrl("https://example.com"),
        observed_at=datetime.now(UTC),
        summary="Public company website",
    )


def test_company_retains_provenance_and_normalizes_identity() -> None:
    observation = evidence()
    company = Company(identity_key=" example.com ", name=" Example ", evidence=(observation,))
    assert company.identity_key == "example.com"
    assert company.evidence == (observation,)


def test_company_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        Company(identity_key="example.com", name="Example", evidence=())


@pytest.mark.parametrize("field", ["identity_key", "name"])
def test_company_rejects_blank_fields(field: str) -> None:
    values = {"identity_key": "example.com", "name": "Example"}
    values[field] = " "
    with pytest.raises(ValidationError):
        Company(**values, evidence=(evidence(),))


def test_evidence_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            source_url=HttpUrl("https://example.com"),
            observed_at=datetime(2026, 1, 1),
            summary="Observation",
        )
