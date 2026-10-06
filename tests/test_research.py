"""Offline deterministic research policies, history, exports and transaction safety."""

import csv
import io
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import JsonValue, ValidationError
from sqlalchemy import Engine
from typer.testing import CliRunner

from examples.research_fixture_demo import run_acceptance
from lead_engine.application.research import CommercialResearchService
from lead_engine.application.research_export import BriefFormat, export_briefs, markdown
from lead_engine.cli.main import app
from lead_engine.domain.audit import AuditStatus, WebsiteAudit
from lead_engine.domain.contact_research import ContactRecommendation, VerificationState
from lead_engine.domain.enums import CampaignType, RoleCategory, SourceType
from lead_engine.domain.models import (
    Campaign,
    Company,
    Contact,
    Entity,
    Evidence,
    Lead,
    LeadScore,
    ScoreComponent,
    Source,
    utc_now,
)
from lead_engine.domain.research import (
    BriefStatus,
    CommercialBrief,
    Opportunity,
    OpportunityPriority,
    OpportunityType,
    ResearchContext,
)
from lead_engine.domain.research_policies import V1ResearchPolicy
from lead_engine.domain.scoring import ScoreBand, ScoringContext
from lead_engine.infrastructure.repositories import SqlAlchemyRepository, SqlAlchemyUnitOfWork

ANCHORS: dict[CampaignType, tuple[str, ...]] = {
    CampaignType.HEALTH: ("HIGH_VALUE_SERVICE", "CORPORATE_CLIENTS"),
    CampaignType.CONSTRUCTION: (
        "HIGH_VALUE_PRODUCT",
        "DISTRIBUTION_NETWORK",
        "B2B_OPERATION",
        "CORPORATE_CLIENTS",
    ),
    CampaignType.HOSPITALITY: ("PRIVATE_EVENTS", "INSTAGRAM_ACTIVE", "MULTIPLE_LOCATIONS"),
}
GAPS = {
    CampaignType.HEALTH: "HAS_RESERVATION_PATH",
    CampaignType.CONSTRUCTION: "HAS_QUOTE_PATH",
    CampaignType.HOSPITALITY: "HAS_RESERVATION_PATH",
}
PRIMARY = {
    CampaignType.HEALTH: OpportunityType.APPOINTMENT_CONVERSION,
    CampaignType.CONSTRUCTION: OpportunityType.QUOTE_CONVERSION,
    CampaignType.HOSPITALITY: OpportunityType.RESERVATION_CONVERSION,
}


def context(
    kind: CampaignType = CampaignType.HEALTH,
    rich: bool = True,
    contact: bool = True,
    score: bool = True,
    audit: bool = True,
    overrides: dict[str, JsonValue] | None = None,
) -> ResearchContext:
    company = Company(
        canonical_name="Fixture business",
        industry="Stored industry",
        city="Lima",
        country="Peru",
        website="https://fixture.example/",
    )
    campaign = Campaign(name="Fixture campaign", campaign_type=kind)
    lead = Lead(company_id=company.id, campaign_id=campaign.id)
    source = Source(source_type=SourceType.MANUAL, url="https://fixture.example/facts")
    facts: dict[str, JsonValue] = (
        {
            **dict.fromkeys(ANCHORS[kind], True),
            GAPS[kind]: False,
            "GOOGLE_REVIEW_COUNT": 500,
            "GOOGLE_RATING": "4.8",
        }
        if rich
        else {}
    )
    facts.update(overrides or {})
    evidence = [
        Evidence(
            company_id=company.id,
            source_id=source.id,
            evidence_type=k,
            raw_value=v,
            statement=f"Explicit fixture observation: {k}",
            confidence=0.9,
            observed_at=utc_now(),
        )
        for k, v in facts.items()
    ]
    person = Contact(
        company_id=company.id,
        full_name="Ana Perez",
        role_title="Marketing Manager",
        role_category=RoleCategory.MARKETING,
    )
    recommendations: tuple[ContactRecommendation, ...] = ()
    if contact:
        role = Evidence(
            company_id=company.id,
            source_id=source.id,
            evidence_type="CONTACT_ROLE",
            statement="Company explicitly identifies Ana Perez as Marketing Manager",
            confidence=0.9,
            observed_at=utc_now(),
        )
        evidence.append(role)
        recommendations = (
            ContactRecommendation(
                contact_id=person.id,
                full_name=person.full_name,
                current_role="Marketing Manager",
                role_category=RoleCategory.MARKETING,
                fit_score=90,
                confidence="HIGH",
                verification=VerificationState.SUPPORTED,
                channels=[],
                evidence_ids=[role.id],
                explanation=["Supported role"],
                warnings=[],
            ),
        )
    score_id = uuid4()
    latest = (
        LeadScore(
            id=score_id,
            lead_id=lead.id,
            total_score=Decimal(85),
            scoring_version="fixture-v1",
            components=(
                ScoreComponent(
                    lead_score_id=score_id,
                    criterion="fixture",
                    points_awarded=Decimal(85),
                    max_points=Decimal(100),
                    explanation="Fixture score",
                ),
            ),
        )
        if score
        else None
    )
    website = (
        WebsiteAudit(
            company_id=company.id,
            source_id=source.id,
            website_url=company.website,
            status=AuditStatus.SUCCESS,
            started_at=utc_now() - timedelta(seconds=1),
            finished_at=utc_now(),
        )
        if audit
        else None
    )
    # Scores calculated after all audit inputs represent current scoring snapshots.
    if latest:
        latest = LeadScore.model_validate({**latest.model_dump(), "calculated_at": utc_now()})
    return ResearchContext(
        observations=ScoringContext(
            lead=lead,
            company=company,
            campaign=campaign,
            evidence=tuple(evidence),
            contacts=(person,) if contact else (),
            audits=(website,) if website else (),
            as_of=utc_now(),
        ),
        sources=(source,),
        latest_score=latest,
        score_band=ScoreBand.A if score else None,
        recommendations=recommendations,
        target_roles=()
        if contact
        else (RoleCategory.MARKETING, RoleCategory.GENERAL_MANAGEMENT, RoleCategory.DIGITAL),
    )


def generate(c: ResearchContext) -> CommercialBrief:
    return V1ResearchPolicy(c.observations.campaign.campaign_type).generate(c)


@pytest.mark.parametrize("kind", list(CampaignType))
def test_campaign_policies(kind: CampaignType) -> None:
    brief = generate(context(kind))
    assert brief.opportunity_type == PRIMARY[kind]
    assert brief.research_version == f"{kind.value.lower()}-research-v1"
    assert (
        brief.status == BriefStatus.READY and brief.opportunity_priority == OpportunityPriority.HIGH
    )
    assert brief.data_completeness == 100
    assert brief.supporting_evidence_ids and brief.primary_opportunity
    assert "Explore" in brief.suggested_contact_angle


def test_secondary_limit_and_source_refs() -> None:
    c = context(
        CampaignType.CONSTRUCTION,
        overrides={
            "CATALOG_PRESENT": False,
            "HAS_PRODUCT_DISCOVERY_PATH": False,
            "HAS_DIRECT_CONTACT_PATH": False,
            "PRIVACY_POLICY_PRESENT": False,
            "MULTIPLE_LOCATIONS": True,
            "MAP_LINK_PRESENT": False,
        },
    )
    brief = generate(c)
    assert len(brief.secondary_opportunities) == 3
    assert (
        brief.primary_opportunity
        and brief.primary_opportunity.opportunity_type == OpportunityType.QUOTE_CONVERSION
    )
    assert {
        cid
        for o in (brief.primary_opportunity,) + brief.secondary_opportunities
        for cid in o.supporting_evidence_ids
    } == set(brief.supporting_evidence_ids)
    assert all(
        citation.source_url == "https://fixture.example/facts"
        for citation in brief.evidence_citations
    )


@pytest.mark.parametrize(
    "overrides", [{"HIGH_VALUE_SERVICE": True}, {"HAS_RESERVATION_PATH": False}, {}]
)
def test_no_unsupported_opportunity(overrides: dict[str, JsonValue]) -> None:
    brief = generate(
        context(rich=False, contact=False, score=False, audit=False, overrides=overrides)
    )
    assert brief.opportunity_type == OpportunityType.NO_CLEAR_OPPORTUNITY
    assert brief.status == BriefStatus.INSUFFICIENT_DATA and brief.primary_opportunity is None
    assert brief.opportunity_priority == OpportunityPriority.NONE
    assert not brief.supporting_evidence_ids


def test_missing_remains_unknown_and_role_fallback() -> None:
    brief = generate(context(rich=False, contact=False, score=False, audit=False))
    assert "Paid advertising activity unknown" in brief.missing_information
    assert "Google review count unknown" in brief.missing_information
    assert brief.target_roles and not brief.decision_maker_recommendations
    assert not any("No booking CTA" in text for text in brief.missing_information)
    assert not brief.decision_maker_available


def test_partial_with_real_opportunity() -> None:
    brief = generate(context(contact=False, score=False, audit=False))
    assert brief.primary_opportunity and brief.status == BriefStatus.PARTIAL
    assert brief.opportunity_priority == OpportunityPriority.LOW


def test_determinism_summary_and_angle() -> None:
    c = context()
    first = generate(c)
    second = generate(c)
    assert first.model_dump(exclude={"id"}) == second.model_dump(exclude={"id"})
    assert "Stored industry" in first.company_summary and "Lima, Peru" in first.company_summary
    assert "appointments" in first.suggested_contact_angle
    assert "website is bad" not in markdown(first)


def test_completeness_known_false_not_strength() -> None:
    c = context(
        rich=False,
        contact=False,
        score=False,
        audit=False,
        overrides=dict.fromkeys(ANCHORS[CampaignType.HEALTH], False),
    )
    brief = generate(c)
    category = next(v for v in brief.completeness_categories if v.category == "commercial_signals")
    assert category.awarded_points == 25
    assert brief.primary_opportunity is None
    assert brief.data_completeness == 40


def test_uncertain_and_old_evidence_not_negative() -> None:
    from dataclasses import replace

    c = context()
    item = next(e for e in c.observations.evidence if e.evidence_type == "HAS_RESERVATION_PATH")
    conflicting = Evidence.model_validate({**item.model_dump(), "id": uuid4(), "raw_value": True})
    ambiguous = replace(
        c, observations=replace(c.observations, evidence=c.observations.evidence + (conflicting,))
    )
    brief = generate(ambiguous)
    assert brief.opportunity_type == OpportunityType.NO_CLEAR_OPPORTUNITY
    assert any("conflicting evidence" in warning for warning in brief.warnings)
    old = Evidence.model_validate(
        {**item.model_dump(), "observed_at": utc_now() - timedelta(days=31)}
    )
    stale = replace(
        c,
        observations=replace(
            c.observations,
            evidence=tuple(old if e.id == item.id else e for e in c.observations.evidence),
        ),
    )
    assert generate(stale).primary_opportunity is None


def test_positive_aggregate_blocks_negative_cta() -> None:
    brief = generate(
        context(overrides={"HAS_RESERVATION_PATH": True, "BOOKING_CTA_PRESENT": False})
    )
    assert brief.primary_opportunity is None


def test_stale_contact_not_high_priority() -> None:
    from dataclasses import replace

    c = context()
    stale = ContactRecommendation.model_validate(
        {**c.recommendations[0].model_dump(), "verification": "STALE"}
    )
    brief = generate(replace(c, recommendations=(stale,)))
    assert (
        not brief.decision_maker_available
        and brief.opportunity_priority == OpportunityPriority.MEDIUM
    )
    assert any("reconfirmation" in warning for warning in brief.warnings)


def test_old_score_limits_status_priority() -> None:
    from dataclasses import replace

    c = context()
    assert c.latest_score
    score = LeadScore.model_validate(
        {**c.latest_score.model_dump(), "calculated_at": utc_now() - timedelta(days=31)}
    )
    brief = generate(replace(c, latest_score=score))
    assert not brief.score_current and brief.status == BriefStatus.PARTIAL
    assert brief.opportunity_priority == OpportunityPriority.LOW


@pytest.mark.parametrize("format", list(BriefFormat))
def test_exports(format: BriefFormat) -> None:
    brief = generate(context())
    result = export_briefs([brief], format)
    if format == BriefFormat.JSON:
        assert CommercialBrief.model_validate(json.loads(result)[0]) == brief
    elif format == BriefFormat.CSV:
        row = list(csv.DictReader(io.StringIO(result)))[0]
        assert (
            row["lead_id"] == str(brief.lead_id)
            and row["primary_opportunity"] == brief.opportunity_type.value
        )
    else:
        assert "## Primary opportunity" in result and "Ana Perez" in result
        assert all(str(eid) in result for eid in brief.supporting_evidence_ids)


def test_export_empty_sets_and_escaping() -> None:
    brief = CommercialBrief.model_validate(
        {**generate(context()).model_dump(), "company_name": "=DANGEROUS()"}
    )
    assert "'=DANGEROUS()" in export_briefs([brief], BriefFormat.CSV)
    assert export_briefs([], BriefFormat.JSON) == "[]\n"
    hostile = CommercialBrief.model_validate(
        {**brief.model_dump(), "company_name": "<script>[link](evil)"}
    )
    assert "<script>" not in markdown(hostile) and "\\[link" in markdown(hostile)


def test_model_invariants() -> None:
    brief = generate(context())
    with pytest.raises(ValidationError):
        CommercialBrief.model_validate({**brief.model_dump(), "data_completeness": 0})
    with pytest.raises(ValidationError):
        CommercialBrief.model_validate({**brief.model_dump(), "supporting_evidence_ids": []})
    with pytest.raises(ValidationError):
        Opportunity(
            opportunity_type=OpportunityType.QUOTE_CONVERSION,
            title="Quote",
            reasons=("one", "two"),
            supporting_evidence_ids=(),
            strength=3,
        )


def test_acceptance_history_roundtrip_and_pipeline(engine: Engine) -> None:
    briefs = run_acceptance(engine)
    assert [b.data_completeness for b in briefs] == [100, 83, 91, 35]
    assert all(b.status == BriefStatus.READY for b in briefs[:3])
    service = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    assert service.latest(briefs[0].lead_id).id != briefs[0].id
    with SqlAlchemyUnitOfWork(engine) as uow:
        for brief in briefs:
            assert uow.repository.get(CommercialBrief, brief.id) == brief
            lead = uow.repository.get(Lead, brief.lead_id)
            assert lead
            assert lead.status == brief.pipeline_status


def test_rescore_reflected_only_in_new_snapshot(engine: Engine) -> None:
    from lead_engine.application.scoring import CommercialScoringService

    briefs = run_acceptance(engine)
    research = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    original = briefs[0]
    score = (
        CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine))
        .score_lead(original.lead_id)
        .score
    )
    fresh = research.research_lead(original.lead_id)
    assert fresh.lead_score_id == score.id and fresh.lead_score_id != original.lead_score_id
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(CommercialBrief, original.id) == original


def test_transaction_rollback(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    briefs = run_acceptance(engine)
    original = SqlAlchemyRepository.add

    def reject(self: SqlAlchemyRepository, entity: Entity) -> None:
        original(self, entity)
        if isinstance(entity, CommercialBrief):
            raise OSError("fixture failure after flush")

    monkeypatch.setattr(SqlAlchemyRepository, "add", reject)
    service = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    before = service.history(briefs[0].lead_id)
    with pytest.raises(OSError):
        service.research_lead(briefs[0].lead_id)
    assert service.history(briefs[0].lead_id) == before


def test_campaign_latest_threshold_and_list(engine: Engine) -> None:
    briefs = run_acceptance(engine)
    service = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    assert not service.research_campaign(briefs[3].campaign_id, min_score=70)
    assert len(service.research_campaign(briefs[0].campaign_id, min_score=70, limit=1)) == 1
    assert len(service.list_campaign(briefs[0].campaign_id)) == 1
    with pytest.raises(ValueError):
        service.research_campaign(briefs[0].campaign_id, limit=0)


def test_cli_and_files(engine: Engine, database_url: str, tmp_path: Path) -> None:
    briefs = run_acceptance(engine)
    lead = briefs[0].lead_id
    runner = CliRunner()
    options = ["--database-url", database_url]
    for command, args in [
        ("lead", ["--lead-id", str(lead)]),
        ("show", ["--lead-id", str(lead), "--format", "json"]),
        ("history", ["--lead-id", str(lead)]),
        ("list", ["--campaign", str(briefs[0].campaign_id)]),
        (
            "campaign",
            ["--campaign", str(briefs[0].campaign_id), "--min-score", "70", "--limit", "1"],
        ),
    ]:
        result = runner.invoke(app, ["research", command, *args, *options])
        assert result.exit_code == 0, result.output
    for suffix in ("md", "json", "csv"):
        target = tmp_path / f"brief.{suffix}"
        result = runner.invoke(
            app, ["research", "show", "--lead-id", str(lead), "--export", str(target), *options]
        )
        assert result.exit_code == 0, result.output
        assert target.read_text(encoding="utf-8")
    assert (
        runner.invoke(
            app, ["research", "show", "--lead-id", str(lead), "--export", str(target), *options]
        ).exit_code
        == 1
    )
    assert (
        runner.invoke(app, ["research", "show", "--lead-id", str(uuid4()), *options]).exit_code == 1
    )


@pytest.mark.parametrize("kind", [CampaignType.HEALTH, CampaignType.HOSPITALITY])
def test_reputation_anchor_without_invented_business(kind: CampaignType) -> None:
    brief = generate(
        context(
            kind,
            rich=False,
            overrides={"GOOGLE_REVIEW_COUNT": 150, "GOOGLE_RATING": "4.5", GAPS[kind]: False},
        )
    )
    assert brief.opportunity_type == PRIMARY[kind]
    assert brief.status == BriefStatus.PARTIAL
    assert "HIGH_VALUE_SERVICE" not in brief.company_summary
    assert brief.primary_opportunity and len(brief.primary_opportunity.supporting_evidence_ids) == 3


def test_failed_coverage_and_future_evidence_not_negative() -> None:
    from dataclasses import replace

    c = context(overrides={"HAS_RESERVATION_PATH": None})
    audit = c.observations.audits[0]
    failed = WebsiteAudit.model_validate({**audit.model_dump(), "status": "FAILED"})
    coverage = Evidence(
        company_id=c.observations.company.id,
        source_id=failed.source_id,
        website_audit_id=failed.id,
        evidence_type="WEBSITE_SIGNAL_COVERAGE",
        statement="Fixture coverage",
        raw_value={"version": "static-homepage-v1", "signals": {"HAS_RESERVATION_PATH": False}},
        confidence=0.9,
        observed_at=utc_now(),
    )
    obs = replace(
        c.observations,
        audits=(failed,),
        evidence=c.observations.evidence + (coverage,),
        as_of=utc_now(),
    )
    brief = generate(replace(c, observations=obs))
    assert brief.primary_opportunity is None
    assert any("did not complete successfully" in warning for warning in brief.warnings)
    future = Evidence.model_validate(
        {
            **coverage.model_dump(),
            "id": uuid4(),
            "website_audit_id": None,
            "evidence_type": "HAS_RESERVATION_PATH",
            "raw_value": False,
            "observed_at": utc_now() + timedelta(days=1),
        }
    )
    assert (
        generate(
            replace(
                c,
                observations=replace(c.observations, evidence=c.observations.evidence + (future,)),
            )
        ).primary_opportunity
        is None
    )


def test_new_commercial_observation_marks_score_outdated() -> None:
    from dataclasses import replace

    c = context()
    evidence = Evidence(
        company_id=c.observations.company.id,
        source_id=c.sources[0].id,
        evidence_type="PAID_ADVERTISING_ACTIVE",
        raw_value=True,
        statement="New explicit public advertising observation",
        confidence=0.9,
        observed_at=utc_now(),
    )
    updated = replace(
        c,
        observations=replace(
            c.observations, evidence=c.observations.evidence + (evidence,), as_of=utc_now()
        ),
    )
    brief = generate(updated)
    assert not brief.score_current and brief.status == BriefStatus.PARTIAL
    assert any("rescore" in warning for warning in brief.warnings)


def test_missing_source_fails_brief_generation() -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="no stored source"):
        generate(replace(context(), sources=()))


def test_policy_cannot_fabricate_citations(engine: Engine) -> None:
    briefs = run_acceptance(engine)

    class ForgedPolicy:
        campaign_type = CampaignType.HEALTH
        version = "health-research-v1"

        def generate(self, c: ResearchContext) -> CommercialBrief:
            result = V1ResearchPolicy(self.campaign_type).generate(c)
            citations = [item.model_dump() for item in result.evidence_citations]
            citations[0]["source_url"] = "https://invented.example/"
            return CommercialBrief.model_validate(
                {**result.model_dump(), "evidence_citations": citations}
            )

    service = CommercialResearchService(
        lambda: SqlAlchemyUnitOfWork(engine), policies={CampaignType.HEALTH: ForgedPolicy()}
    )
    before = service.history(briefs[0].lead_id)
    with pytest.raises(ValueError, match="differs from stored"):
        service.research_lead(briefs[0].lead_id)
    assert service.history(briefs[0].lead_id) == before


def test_latest_low_score_filters_campaign_and_keeps_history(engine: Engine) -> None:
    briefs = run_acceptance(engine)
    original = briefs[0]
    identity = uuid4()
    score = LeadScore(
        id=identity,
        lead_id=original.lead_id,
        total_score=Decimal(5),
        scoring_version="fixture-v2",
        components=(
            ScoreComponent(
                lead_score_id=identity,
                criterion="fixture",
                points_awarded=Decimal(5),
                max_points=Decimal(100),
                explanation="New low score",
            ),
        ),
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(score)
        uow.commit()
    service = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    before = service.latest(original.lead_id)
    assert before.lead_score_id != score.id
    assert not service.research_campaign(original.campaign_id, min_score=70)
    fresh = service.research_lead(original.lead_id)
    assert fresh.latest_lead_score == 5 and fresh.score_band == ScoreBand.E
    assert fresh.opportunity_priority == OpportunityPriority.LOW
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(CommercialBrief, original.id) == original


def test_additive_migration_preserves_core_data(database_url: str) -> None:
    from alembic import command

    from lead_engine.infrastructure.database import build_engine
    from lead_engine.infrastructure.schema import (
        database_revision,
        migration_config,
        upgrade_database,
    )

    command.upgrade(migration_config(database_url), "0004")
    engine = build_engine(database_url)
    try:
        c = context()
        with SqlAlchemyUnitOfWork(engine) as uow:
            for entity in (
                c.observations.company,
                c.observations.campaign,
                c.observations.lead,
                *c.sources,
                *c.observations.contacts,
            ):
                uow.repository.add(entity)
            for entity in c.observations.evidence:
                uow.repository.add(entity)
            assert c.latest_score
            uow.repository.add(c.latest_score)
            uow.commit()
        upgrade_database(database_url)
        assert database_revision(engine) == "0006"
        command.downgrade(migration_config(database_url), "0004")
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.get(LeadScore, c.latest_score.id) == c.latest_score
            assert uow.repository.get(Company, c.observations.company.id) == c.observations.company
        upgrade_database(database_url)
    finally:
        engine.dispose()


def test_primary_explains_corroborating_reputation_and_growth() -> None:
    c = context(overrides={"RECENT_EXPANSION": True})
    brief = generate(c)
    assert brief.primary_opportunity
    assert any("reputation signal" in reason for reason in brief.primary_opportunity.reasons)
    assert any("Recent expansion" in reason for reason in brief.primary_opportunity.reasons)
    expected = {
        e.id
        for e in c.observations.evidence
        if e.evidence_type in {"GOOGLE_REVIEW_COUNT", "GOOGLE_RATING", "RECENT_EXPANSION"}
    }
    assert expected <= set(brief.primary_opportunity.supporting_evidence_ids)
    assert "Stored reputation" in brief.company_summary
