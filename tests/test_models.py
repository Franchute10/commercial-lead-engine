from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from lead_engine.domain.enums import LeadStatus, PipelineStage
from lead_engine.domain.identity import normalize_domain, normalize_name, normalize_url
from lead_engine.domain.models import Company, Contact, Evidence, Lead, LeadScore, ScoreComponent


def test_company_normalization_preserves_original_names() -> None:
    company = Company(
        canonical_name="  Clínica Ágil SAC  ", primary_domain="HTTPS://WWW.EXAMPLE.COM/path"
    )
    assert company.canonical_name == "Clínica Ágil SAC"
    assert company.primary_domain == "example.com"
    assert normalize_name(company.canonical_name) == "clinica agil sac"
    assert normalize_url("HTTPS://Example.COM:443/path#top") == "https://example.com/path"


@pytest.mark.parametrize("name", ["", "  ", "---"])
def test_invalid_company_name(name: str) -> None:
    with pytest.raises(ValidationError):
        Company(canonical_name=name)


def test_unknown_company_details_are_allowed() -> None:
    assert Company(canonical_name="Example").country is None


@pytest.mark.parametrize(
    "value", ["localhost", "https://user:secret@example.com", "ftp://example.com"]
)
def test_invalid_domains(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_domain(value)


def test_website_domain_conflict() -> None:
    with pytest.raises(ValidationError):
        Company(canonical_name="Example", website="https://example.com", primary_domain="other.com")


def test_provenance_required() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            {
                "company_id": uuid4(),
                "statement": "No appointment CTA",
                "evidence_type": "CTA",
                "confidence": 1,
                "observed_at": datetime.now(UTC),
            }
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf")])
def test_confidence_bounds(confidence: float) -> None:
    with pytest.raises(ValidationError):
        Evidence(
            company_id=uuid4(),
            source_id=uuid4(),
            evidence_type="REVIEWS",
            statement="427 reviews",
            confidence=confidence,
            observed_at=datetime.now(UTC),
        )


def test_evidence_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            company_id=uuid4(),
            source_id=uuid4(),
            evidence_type="REVIEWS",
            statement="427 reviews",
            confidence=1,
            observed_at=datetime(2026, 1, 1),
        )


def score_data(total: str = "70", awarded: str = "70", maximum: str = "100") -> dict[str, object]:
    score_id = uuid4()
    return {
        "id": score_id,
        "lead_id": uuid4(),
        "total_score": total,
        "scoring_version": "v1",
        "components": (
            ScoreComponent(
                lead_score_id=score_id,
                criterion="commercial gap",
                points_awarded=Decimal(awarded),
                max_points=Decimal(maximum),
                explanation="Manual evaluation",
            ),
        ),
    }


@pytest.mark.parametrize("total", ["-1", "101", "NaN", "Infinity"])
def test_score_limits(total: str) -> None:
    with pytest.raises(ValidationError):
        LeadScore.model_validate(score_data(total=total))


def test_total_is_reproducible() -> None:
    assert LeadScore.model_validate(score_data()).total_score == Decimal(70)
    with pytest.raises(ValidationError):
        LeadScore.model_validate(score_data(total="69"))
    with pytest.raises(ValidationError):
        LeadScore.model_validate(score_data(maximum="90"))


def test_component_limits_and_parent() -> None:
    with pytest.raises(ValidationError):
        score_data(awarded="101")
    data = score_data()
    data["id"] = uuid4()
    with pytest.raises(ValidationError):
        LeadScore.model_validate(data)


def test_empty_and_duplicate_components() -> None:
    data = score_data()
    data["components"] = ()
    with pytest.raises(ValidationError):
        LeadScore.model_validate(data)
    data = score_data()
    component = ScoreComponent(
        lead_score_id=uuid4(),
        criterion="same",
        points_awarded=Decimal(20),
        max_points=Decimal(50),
        explanation="Manual",
    )
    data["components"] = (component, component)
    with pytest.raises(ValidationError):
        LeadScore.model_validate(data)


def test_status_validation_and_single_pipeline_state() -> None:
    with pytest.raises(ValidationError):
        Lead.model_validate({"company_id": uuid4(), "campaign_id": uuid4(), "status": "BOGUS"})
    assert PipelineStage is LeadStatus
    assert Contact(company_id=uuid4(), full_name="Person").role_category.value == "UNKNOWN"


def test_timestamps_and_priority() -> None:
    with pytest.raises(ValidationError):
        Company(
            canonical_name="Example",
            created_at=datetime(2026, 2, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValidationError):
        Lead(company_id=uuid4(), campaign_id=uuid4(), priority=101)
