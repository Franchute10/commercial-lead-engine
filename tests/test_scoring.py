import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from examples.scoring_fixture_demo import create_fixtures
from lead_engine.application.scoring import CommercialScoringService
from lead_engine.application.services import LeadService
from lead_engine.cli.main import app
from lead_engine.domain.audit import AuditStatus, WebsiteAudit
from lead_engine.domain.enums import CampaignType, RoleCategory
from lead_engine.domain.models import (
    Campaign,
    Company,
    Contact,
    Evidence,
    Lead,
    LeadScore,
    Source,
    utc_now,
)
from lead_engine.domain.policies import DEFAULT_POLICIES, HEALTH_V1, Observations
from lead_engine.domain.scoring import ScoreBand, ScoringContext, SignalState, score_band
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork


def context(
    kind: CampaignType = CampaignType.HEALTH, evidence: tuple[Evidence, ...] = ()
) -> ScoringContext:
    company_id = evidence[0].company_id if evidence else uuid4()
    company = Company(id=company_id, canonical_name="Test")
    campaign = Campaign(name="Test", campaign_type=kind)
    lead = Lead(company_id=company.id, campaign_id=campaign.id)
    return ScoringContext(lead, company, campaign, evidence, (), (), utc_now())


def observation(
    signal: str, value: object, *, company_id: object = None, confidence: float = 0.9, days: int = 0
) -> Evidence:
    return Evidence.model_validate(
        {
            "company_id": company_id or uuid4(),
            "source_id": uuid4(),
            "evidence_type": signal,
            "statement": "Manual test observation",
            "raw_value": value,
            "confidence": confidence,
            "observed_at": utc_now() - timedelta(days=days),
        }
    )


@pytest.mark.parametrize("kind", list(CampaignType))
def test_policy_maxima_and_determinism(kind: CampaignType) -> None:
    policy = DEFAULT_POLICIES[kind]
    data = context(kind)
    first, summary = policy.calculate(data)
    second, again = policy.calculate(data)
    assert sum(item.max_points for item in first.components) == 100
    assert first.total_score == second.total_score == 0
    assert first.scoring_version == f"{kind.value.lower()}-v1"
    assert [
        (item.criterion, item.points_awarded, item.explanation) for item in first.components
    ] == [(item.criterion, item.points_awarded, item.explanation) for item in second.components]
    assert summary == again and summary.completeness_percent == 0
    assert all("UNKNOWN" in item.explanation for item in first.components)


@pytest.mark.parametrize(
    "count,expected",
    [(0, 0), (9, 0), (10, 2), (49, 2), (50, 4), (199, 4), (200, 6), (499, 6), (500, 8)],
)
def test_reputation_thresholds(count: int, expected: int) -> None:
    evidence = observation("GOOGLE_REVIEW_COUNT", count)
    score, _ = HEALTH_V1.calculate(context(evidence=(evidence,)))
    component = next(item for item in score.components if item.criterion == "REPUTATION_SIGNAL")
    assert component.points_awarded == expected and evidence.id in component.evidence_ids


@pytest.mark.parametrize(
    "rating,count,expected", [("4.5", 10, 4), ("4.0", 10, 3), ("3.9", 10, 2), ("4.8", 0, 0)]
)
def test_rating_requires_review_count(rating: str, count: int, expected: int) -> None:
    reviews = observation("GOOGLE_REVIEW_COUNT", count)
    value = observation("GOOGLE_RATING", rating, company_id=reviews.company_id)
    score, _ = HEALTH_V1.calculate(context(evidence=(reviews, value)))
    assert (
        next(
            item for item in score.components if item.criterion == "REPUTATION_SIGNAL"
        ).points_awarded
        == expected
    )


def test_missing_not_false_and_explicit_false_has_known_coverage() -> None:
    missing, incomplete = HEALTH_V1.calculate(context())
    negative = observation("HIGH_VALUE_SERVICE", False)
    zero, known = HEALTH_V1.calculate(context(evidence=(negative,)))
    assert missing.total_score == zero.total_score == 0
    assert incomplete.completeness_percent == 0 and known.completeness_percent == 12
    assert (
        "ABSENT"
        in next(
            item for item in zero.components if item.criterion == "COMMERCIAL_VALUE"
        ).explanation
    )


def test_contradictions_recent_confidence_and_unresolved() -> None:
    older = observation("HIGH_VALUE_SERVICE", True, days=2, confidence=0.9)
    newer = observation(
        "HIGH_VALUE_SERVICE", False, company_id=older.company_id, confidence=0.9, days=1
    )
    facts = Observations(context(evidence=(older, newer)))
    resolved = facts.fact("HIGH_VALUE_SERVICE")
    assert resolved.state == SignalState.ABSENT and "conflict resolved" in resolved.note
    weaker = newer.model_copy(update={"confidence": 0.8})
    uncertain = Observations(context(evidence=(older, weaker))).fact("HIGH_VALUE_SERVICE")
    assert uncertain.state == SignalState.UNCERTAIN
    score, _ = HEALTH_V1.calculate(context(evidence=(older, weaker)))
    assert score.total_score == 0
    assert {older.id, newer.id} <= set(
        next(item for item in score.components if item.criterion == "COMMERCIAL_VALUE").evidence_ids
    )


def test_simultaneous_conflict_is_uncertain_and_low_confidence_ignored() -> None:
    first = observation("HIGH_VALUE_SERVICE", True)
    second = first.model_copy(update={"id": uuid4(), "raw_value": False})
    assert (
        Observations(context(evidence=(first, second))).fact("HIGH_VALUE_SERVICE").state
        == SignalState.UNCERTAIN
    )
    weak = first.model_copy(update={"confidence": 0.5})
    assert (
        Observations(context(evidence=(weak,))).fact("HIGH_VALUE_SERVICE").state
        == SignalState.UNKNOWN
    )


def test_stale_and_future_observations_are_unknown() -> None:
    old = observation("INSTAGRAM_ACTIVE", True, days=91)
    assert (
        Observations(context(evidence=(old,))).fact("INSTAGRAM_ACTIVE").state == SignalState.UNKNOWN
    )
    future = old.model_copy(update={"observed_at": utc_now() + timedelta(days=1)})
    assert (
        Observations(context(evidence=(future,))).fact("INSTAGRAM_ACTIVE").state
        == SignalState.UNKNOWN
    )


@pytest.mark.parametrize(
    "role,authority,reachable,expected",
    [
        (RoleCategory.OWNER, True, True, 10),
        (RoleCategory.MARKETING, True, True, 8),
        (RoleCategory.MEDICAL_DIRECTOR, True, True, 8),
        (RoleCategory.UNKNOWN, True, True, 0),
        (RoleCategory.OWNER, False, True, 0),
        (RoleCategory.OWNER, True, False, 0),
    ],
)
def test_decision_maker_requires_contact_role_and_explicit_authority(
    role: RoleCategory, authority: bool, reachable: bool, expected: int
) -> None:
    base = context()
    contact = Contact(company_id=base.company.id, full_name="Person", role_category=role)
    evidence = observation(
        "DECISION_MAKER_ACCESS",
        {"contact_id": str(contact.id), "authority_confirmed": authority, "reachable": reachable},
        company_id=base.company.id,
    )
    data = replace(base, evidence=(evidence,), contacts=(contact,), as_of=utc_now())
    score, _ = HEALTH_V1.calculate(data)
    assert (
        next(
            item for item in score.components if item.criterion == "DECISION_MAKER_ACCESS"
        ).points_awarded
        == expected
    )
    no_evidence, _ = HEALTH_V1.calculate(replace(data, evidence=()))
    assert no_evidence.total_score == 0


@pytest.mark.parametrize(
    "total,band",
    [
        (0, "E"),
        (39, "E"),
        (40, "D"),
        (54, "D"),
        (55, "C"),
        (69, "C"),
        (70, "B"),
        (84, "B"),
        (85, "A"),
        (100, "A"),
    ],
)
def test_bands(total: int, band: str) -> None:
    assert score_band(Decimal(total), (85, 70, 55, 40)) == ScoreBand(band)


def test_custom_band_profile_requires_new_version() -> None:
    with pytest.raises(ValueError):
        replace(HEALTH_V1, bands=(90, 75, 60, 45))
    policy = replace(HEALTH_V1, version="health-v2", bands=(90, 75, 60, 45))
    assert policy.calculate(context())[1].bands == (90, 75, 60, 45)


def test_four_fixtures_opportunities_history_version_readiness_and_no_lead_transition(
    engine: Engine,
) -> None:
    leads = create_fixtures(engine)
    scorer = CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine))
    results = [scorer.score_lead(item.id) for item in leads]
    assert [result.score.total_score for result in results] == [91, 97, 96, 0]
    assert [result.summary.completeness_percent for result in results] == [100, 100, 100, 0]
    for result in results:
        for component in result.score.components:
            if component.points_awarded > 0:
                assert component.evidence_ids
        again = scorer.score_lead(result.score.lead_id)
        assert again.score.total_score == result.score.total_score
        assert len(scorer.history(result.score.lead_id)) == 2
    policies = dict(DEFAULT_POLICIES)
    policies[CampaignType.HEALTH] = replace(HEALTH_V1, version="health-v2")
    changed = CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine), policies).score_lead(
        leads[0].id
    )
    assert changed.score.scoring_version == "health-v2"
    assert len(scorer.history(leads[0].id)) == 3
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(LeadScore, changed.score.id) == changed.score
        assert uow.repository.get(Lead, leads[0].id) == leads[0]


def test_older_audit_without_coverage_does_not_imply_absence(engine: Engine) -> None:
    leads = create_fixtures(engine)
    with SqlAlchemyUnitOfWork(engine) as uow:
        company = uow.repository.get(Company, leads[0].company_id)
        campaign = uow.repository.get(Campaign, leads[0].campaign_id)
        assert company is not None and campaign is not None
        evidence = tuple(
            item
            for item in uow.repository.list(Evidence, company_id=company.id)
            if item.evidence_type != "WEBSITE_SIGNAL_COVERAGE"
        )
        data = ScoringContext(
            leads[0],
            company,
            campaign,
            evidence,
            tuple(uow.repository.list(Contact)),
            tuple(uow.repository.list(WebsiteAudit, company_id=company.id)),
            utc_now(),
        )
    score, _ = HEALTH_V1.calculate(data)
    conversion = next(
        item for item in score.components if item.criterion == "CONVERSION_OPPORTUNITY"
    )
    assert conversion.points_awarded == 0 and "UNKNOWN" in conversion.explanation


def test_failed_audit_never_becomes_negative_website_evidence(engine: Engine) -> None:
    leads = create_fixtures(engine)
    with SqlAlchemyUnitOfWork(engine) as uow:
        company = uow.repository.get(Company, leads[0].company_id)
        campaign = uow.repository.get(Campaign, leads[0].campaign_id)
        assert company is not None and campaign is not None
        audits = tuple(
            audit.model_copy(update={"status": AuditStatus.FAILED})
            for audit in uow.repository.list(WebsiteAudit, company_id=company.id)
        )
        data = ScoringContext(
            leads[0],
            company,
            campaign,
            tuple(uow.repository.list(Evidence, company_id=company.id)),
            (),
            audits,
            utc_now(),
        )
    score, _ = HEALTH_V1.calculate(data)
    assert (
        next(
            item for item in score.components if item.criterion == "CONVERSION_OPPORTUNITY"
        ).points_awarded
        == 0
    )


def test_score_transaction_rollback_after_component_insert(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    leads = create_fixtures(engine)
    original = LeadService.record_lead_score

    def fail(self: LeadService, score: LeadScore) -> LeadScore:
        original(self, score)
        raise RuntimeError("Fail after persistence")

    monkeypatch.setattr(LeadService, "record_lead_score", fail)
    with pytest.raises(RuntimeError):
        CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine)).score_lead(leads[0].id)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(LeadScore) == []


def test_manual_evidence_and_score_cli(engine: Engine, database_url: str) -> None:
    leads = create_fixtures(engine)
    runner = CliRunner()
    options = ["--database-url", database_url]
    for signal, raw, value_type in (
        ("GOOGLE_REVIEW_COUNT", "427", "integer"),
        ("GOOGLE_RATING", "4.7", "decimal"),
        ("RECENT_REBRAND", "true", "boolean"),
        ("COMMERCIAL_NOTE", "Public observation", "text"),
    ):
        result = runner.invoke(
            app,
            [
                "evidence",
                "add",
                "--company-id",
                str(leads[0].company_id),
                "--type",
                signal,
                "--value",
                raw,
                "--value-type",
                value_type,
                *options,
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["source_id"]
    result = runner.invoke(
        app, ["score", "campaign", "--campaign", "Fixture HEALTH", "--min-score", "70", *options]
    )
    assert result.exit_code == 0, result.output
    assert "Recorded scores: 1" in result.stdout
    assert (
        runner.invoke(app, ["score", "explain", "--lead-id", str(leads[0].id), *options]).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["score", "list", "--campaign", "Fixture HEALTH", "--band", "A", *options]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["score", "history", "--lead-id", str(leads[0].id), *options]).exit_code
        == 0
    )


@pytest.mark.parametrize(
    "signal,value,type_name",
    [
        ("GOOGLE_REVIEW_COUNT", "-1", "integer"),
        ("GOOGLE_REVIEW_COUNT", "true", "boolean"),
        ("GOOGLE_RATING", "6", "decimal"),
        ("INSTAGRAM_ACTIVE", "active", "text"),
        ("INSTAGRAM_ACTIVE", "yes", "boolean"),
    ],
)
def test_invalid_manual_signal_rolls_back_source(
    engine: Engine, database_url: str, signal: str, value: str, type_name: str
) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        company = LeadService(uow).upsert_company(Company(canonical_name="Test"))
        uow.commit()
    result = CliRunner().invoke(
        app,
        [
            "evidence",
            "add",
            "--company-id",
            str(company.id),
            "--type",
            signal,
            "--value",
            value,
            "--value-type",
            type_name,
            "--database-url",
            database_url,
        ],
    )
    assert result.exit_code == 1
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(Source) == [] and uow.repository.list(Evidence) == []


def test_hospitality_reputation_uses_coarse_integer_points() -> None:
    reviews = observation("GOOGLE_REVIEW_COUNT", 200)
    rating = observation("GOOGLE_RATING", "4.2", company_id=reviews.company_id)
    score, _ = DEFAULT_POLICIES[CampaignType.HOSPITALITY].calculate(
        context(CampaignType.HOSPITALITY, (reviews, rating))
    )
    assert (
        next(
            item for item in score.components if item.criterion == "REPUTATION_SIGNAL"
        ).points_awarded
        == 12
    )


def test_manual_absence_is_supported_but_unknown_other_paths_are_not() -> None:
    business = observation("HIGH_VALUE_SERVICE", True)
    absent = observation("HAS_RESERVATION_PATH", False, company_id=business.company_id)
    score, _ = HEALTH_V1.calculate(context(evidence=(business, absent)))
    component = next(
        item for item in score.components if item.criterion == "CONVERSION_OPPORTUNITY"
    )
    assert component.points_awarded == 8
    assert {business.id, absent.id} <= set(component.evidence_ids)
    assert "CONTACT_FORM_PRESENT=UNKNOWN" in component.explanation


def test_business_absence_does_not_award_opportunity_points() -> None:
    names = (
        "HIGH_VALUE_SERVICE",
        "HIGH_VALUE_PRODUCT",
        "MULTIPLE_LOCATIONS",
        "PRIVATE_EVENTS",
        "ECOMMERCE",
        "DISTRIBUTION_NETWORK",
        "B2B_OPERATION",
        "CORPORATE_CLIENTS",
        "EXPORT_ACTIVITY",
    )
    company_id = uuid4()
    evidence = tuple(observation(name, False, company_id=company_id) for name in names)
    evidence += (
        observation("GOOGLE_REVIEW_COUNT", 0, company_id=company_id),
        observation("HAS_RESERVATION_PATH", False, company_id=company_id),
    )
    data = context(evidence=evidence)
    assert Observations(data).commercial_anchor().state == SignalState.ABSENT
    assert HEALTH_V1.calculate(data)[0].total_score == 0
