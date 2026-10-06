"""Offline shortlist ranking, timing, provenance and persistence tests."""

import csv
import io
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue, ValidationError
from sqlalchemy import Engine
from typer.testing import CliRunner

from examples.shortlist_fixture_demo import run_acceptance
from lead_engine.application.shortlist import DailyShortlistService
from lead_engine.application.shortlist_export import ShortlistFormat, export_runs
from lead_engine.cli.main import app
from lead_engine.domain.audit import AuditStatus, WebsiteAudit
from lead_engine.domain.contact_research import (
    ContactRecommendation,
    PublicChannel,
    VerificationState,
)
from lead_engine.domain.enums import (
    CampaignType,
    InteractionType,
    LeadStatus,
    RoleCategory,
    SourceType,
)
from lead_engine.domain.models import (
    Campaign,
    Company,
    Contact,
    Entity,
    Evidence,
    Lead,
    LeadInteraction,
    LeadScore,
    ScoreComponent,
    Source,
    utc_now,
)
from lead_engine.domain.research import (
    CommercialBrief,
    OpportunityType,
    ResearchContext,
)
from lead_engine.domain.research_policies import V1ResearchPolicy
from lead_engine.domain.scoring import ScoreSummary, ScoringContext, score_band
from lead_engine.domain.shortlist import (
    DailyShortlistRun,
    NextAction,
    ShortlistContext,
    ShortlistDecision,
    ShortlistFilters,
    ShortlistSettings,
    ShortlistSuppression,
    SuppressionReason,
)
from lead_engine.domain.shortlist_policy import V1ShortlistPolicy
from lead_engine.infrastructure.repositories import SqlAlchemyRepository, SqlAlchemyUnitOfWork


def context(points: int = 91) -> ShortlistContext:
    company = Company(
        canonical_name="Business",
        city="Lima",
        country="Peru",
        industry="Health",
        website="https://business.example/",
    )
    campaign = Campaign(name="Health campaign", campaign_type=CampaignType.HEALTH)
    lead = Lead(company_id=company.id, campaign_id=campaign.id, status=LeadStatus.QUALIFIED)
    source = Source(source_type=SourceType.WEBSITE, url="https://business.example/team")
    signals: dict[str, JsonValue] = {
        "HIGH_VALUE_SERVICE": True,
        "CORPORATE_CLIENTS": True,
        "HAS_RESERVATION_PATH": False,
        "GOOGLE_REVIEW_COUNT": 500,
        "GOOGLE_RATING": "4.8",
    }
    evidence = [
        Evidence(
            company_id=company.id,
            source_id=source.id,
            evidence_type=k,
            statement=f"Explicit fixture {k}",
            raw_value=v,
            confidence=0.9,
            observed_at=utc_now(),
        )
        for k, v in signals.items()
    ]
    person = Contact(
        company_id=company.id,
        full_name="Ana Perez",
        role_title="Owner",
        role_category=RoleCategory.OWNER,
    )
    role = Evidence(
        company_id=company.id,
        source_id=source.id,
        evidence_type="CONTACT_ROLE",
        statement="Owner",
        raw_value={"contact_id": str(person.id)},
        confidence=0.9,
        observed_at=utc_now(),
    )
    link = Evidence(
        company_id=company.id,
        source_id=source.id,
        evidence_type="CONTACT_PUBLIC_PROFILE",
        statement="https://linkedin.com/in/ana",
        raw_value={"contact_id": str(person.id)},
        confidence=0.9,
        observed_at=utc_now(),
    )
    evidence.extend([role, link])
    recommendation = ContactRecommendation(
        contact_id=person.id,
        full_name=person.full_name,
        current_role="Owner",
        role_category=RoleCategory.OWNER,
        fit_score=90,
        confidence="HIGH",
        verification=VerificationState.SUPPORTED,
        channels=[
            PublicChannel(channel_type="LINKEDIN", value=link.statement, evidence_id=link.id)
        ],
        evidence_ids=[role.id, link.id],
        explanation=["Explicit fixture role"],
        warnings=[],
    )
    audit = WebsiteAudit(
        company_id=company.id,
        source_id=source.id,
        website_url=company.website,
        status=AuditStatus.SUCCESS,
        started_at=utc_now() - timedelta(seconds=1),
        finished_at=utc_now(),
    )
    identity = uuid4()
    calculated = utc_now()
    summary = ScoreSummary(
        band=score_band(Decimal(points), (85, 70, 55, 40)),
        completeness_percent=80,
        known_weight=80,
        input_cutoff=calculated.isoformat(),
    )
    score = LeadScore(
        id=identity,
        lead_id=lead.id,
        total_score=Decimal(points),
        scoring_version="fixture",
        calculated_at=calculated,
        explanation=summary.model_dump_json(),
        components=(
            ScoreComponent(
                lead_score_id=identity,
                criterion="fixture",
                points_awarded=Decimal(points),
                max_points=Decimal(100),
                explanation="Synthetic explicit score",
            ),
        ),
    )
    research = ResearchContext(
        observations=ScoringContext(
            lead=lead,
            company=company,
            campaign=campaign,
            evidence=tuple(evidence),
            contacts=(person,),
            audits=(audit,),
            as_of=utc_now(),
        ),
        sources=(source,),
        latest_score=score,
        score_band=summary.band,
        recommendations=(recommendation,),
        target_roles=(),
    )
    brief = V1ResearchPolicy(CampaignType.HEALTH).generate(research)
    return ShortlistContext(
        lead=lead,
        company=company,
        campaign=campaign,
        score=score,
        brief=brief,
        contacts=(recommendation,),
        target_roles=(),
        interactions=(),
        audits=(audit,),
        evidence=tuple(evidence),
        suppressions=(),
        as_of=utc_now(),
    )


def evaluate(
    c: ShortlistContext,
    filters: ShortlistFilters | None = None,
    settings: ShortlistSettings | None = None,
) -> ShortlistDecision:
    return V1ShortlistPolicy().evaluate(
        c, filters or ShortlistFilters(), settings or ShortlistSettings()
    )


def interaction(
    c: ShortlistContext, kind: InteractionType, days: int = 1, outcome: str | None = None
) -> LeadInteraction:
    return LeadInteraction(
        lead_id=c.lead.id,
        interaction_type=kind,
        occurred_at=c.as_of - timedelta(days=days),
        outcome=outcome,
    )


@pytest.mark.parametrize(
    "score,points,band",
    [
        (91, 30, "A"),
        (85, 30, "A"),
        (84, 25, "B"),
        (70, 25, "B"),
        (55, 17, "C"),
        (40, 8, "D"),
        (39, 0, "E"),
    ],
)
def test_commercial_potential_and_bands(score: int, points: int, band: str) -> None:
    item = evaluate(context(score)).item
    assert item.components[0].points == points and item.score_band and item.score_band.value == band
    assert item.score_completeness == 80


def test_ready_vs_partial_and_separate_score() -> None:
    c = context()
    assert c.brief
    ready = evaluate(c).item
    partial = evaluate(
        replace(
            c, brief=CommercialBrief.model_validate({**c.brief.model_dump(), "status": "PARTIAL"})
        )
    ).item
    assert ready.shortlist_priority_score == 100
    assert partial.shortlist_priority_score < ready.shortlist_priority_score
    assert partial.recommended_next_action == NextAction.RESEARCH_MORE
    assert ready.latest_score == 91


def test_contact_role_fallback_and_no_channel() -> None:
    c = context()
    roles = evaluate(replace(c, contacts=(), target_roles=(RoleCategory.MARKETING,))).item
    assert (
        roles.components[2].points == 4
        and roles.recommended_next_action == NextAction.FIND_DECISION_MAKER
    )
    no_channel = ContactRecommendation.model_validate(
        {**c.contacts[0].model_dump(), "channels": []}
    )
    item = evaluate(replace(c, contacts=(no_channel,))).item
    assert (
        item.components[2].points == 8
        and item.recommended_next_action == NextAction.REVIEW_MANUALLY
    )


@pytest.mark.parametrize("priority,points", [("HIGH", 15), ("MEDIUM", 10), ("LOW", 4), ("NONE", 0)])
def test_opportunity_points(priority: str, points: int) -> None:
    c = context()
    assert c.brief
    brief = CommercialBrief.model_validate(
        {**c.brief.model_dump(), "opportunity_priority": priority}
    )
    assert evaluate(replace(c, brief=brief)).item.components[3].points == points


@pytest.mark.parametrize(
    "kind",
    [
        InteractionType.LINKEDIN_CONNECTION,
        InteractionType.LINKEDIN_MESSAGE,
        InteractionType.EMAIL,
        InteractionType.WHATSAPP,
        InteractionType.PHONE_CALL,
        InteractionType.FOLLOW_UP,
        InteractionType.PROPOSAL_SENT,
    ],
)
def test_outbound_cooldowns(kind: InteractionType) -> None:
    c = context()
    decision = evaluate(replace(c, interactions=(interaction(c, kind),)))
    assert SuppressionReason.RECENT_CONTACT in decision.suppression_reasons
    assert (
        decision.item.recommended_next_action == NextAction.NO_ACTION
        and decision.item.eligible_again_at
    )


def test_cooldown_exact_boundary_and_note_does_not_reset() -> None:
    c = context()
    email = interaction(c, InteractionType.EMAIL, 7)
    decision = evaluate(replace(c, interactions=(email, interaction(c, InteractionType.NOTE, 0))))
    assert (
        not decision.suppression_reasons
        and decision.item.recommended_next_action == NextAction.FOLLOW_UP
    )
    newer_note = interaction(c, InteractionType.NOTE, 0)
    assert (
        SuppressionReason.RECENT_CONTACT
        in evaluate(
            replace(c, interactions=(interaction(c, InteractionType.EMAIL, 1), newer_note))
        ).suppression_reasons
    )


@pytest.mark.parametrize(
    "stage,reason",
    [
        (LeadStatus.WON, SuppressionReason.WON),
        (LeadStatus.LOST, SuppressionReason.LOST),
        (LeadStatus.ARCHIVED, SuppressionReason.ARCHIVED),
        (LeadStatus.REJECTED, SuppressionReason.PIPELINE_NOT_ACTIONABLE),
    ],
)
def test_closed_pipeline(stage: LeadStatus, reason: SuppressionReason) -> None:
    c = context()
    lead = Lead.model_validate({**c.lead.model_dump(), "status": stage})
    decision = evaluate(replace(c, lead=lead))
    assert reason in decision.suppression_reasons
    assert decision.item.recommended_next_action == NextAction.NO_ACTION


@pytest.mark.parametrize(
    "stage,action",
    [
        (LeadStatus.RESPONDED, NextAction.REVIEW_MANUALLY),
        (LeadStatus.MEETING, NextAction.PREPARE_MEETING),
    ],
)
def test_internal_actions_during_cooldown(stage: LeadStatus, action: NextAction) -> None:
    c = context()
    lead = Lead.model_validate({**c.lead.model_dump(), "status": stage})
    decision = evaluate(
        replace(c, lead=lead, interactions=(interaction(c, InteractionType.EMAIL),))
    )
    assert SuppressionReason.RECENT_CONTACT not in decision.suppression_reasons
    assert decision.item.recommended_next_action == action


def test_proposal_and_contacted_are_not_first_outreach() -> None:
    c = context()
    for stage in (LeadStatus.PROPOSAL, LeadStatus.CONTACTED):
        lead = Lead.model_validate({**c.lead.model_dump(), "status": stage})
        assert (
            evaluate(replace(c, lead=lead)).item.recommended_next_action
            == NextAction.REVIEW_MANUALLY
        )
    proposal = Lead.model_validate({**c.lead.model_dump(), "status": "PROPOSAL"})
    decision = evaluate(
        replace(c, lead=proposal, interactions=(interaction(c, InteractionType.PROPOSAL_SENT, 10),))
    )
    assert (
        not decision.suppression_reasons
        and decision.item.recommended_next_action == NextAction.FOLLOW_UP
    )


def test_linkedin_acceptance_not_inferred() -> None:
    c = context()
    pending = evaluate(
        replace(c, interactions=(interaction(c, InteractionType.LINKEDIN_CONNECTION, 7),))
    )
    assert pending.item.recommended_next_action == NextAction.REVIEW_MANUALLY
    accepted = evaluate(
        replace(
            c, interactions=(interaction(c, InteractionType.LINKEDIN_CONNECTION, 7, "ACCEPTED"),)
        )
    )
    assert accepted.item.recommended_next_action == NextAction.SEND_LINKEDIN_MESSAGE


@pytest.mark.parametrize(
    "channel,value,kind,action",
    [
        ("EMAIL", "ana@example.com", "CONTACT_PUBLIC_EMAIL", NextAction.SEND_EMAIL),
        ("PHONE", "+51 123456789", "CONTACT_PUBLIC_PHONE", NextAction.MAKE_PHONE_CALL),
        (
            "WHATSAPP",
            "https://wa.me/51123456789",
            "CONTACT_PUBLIC_WHATSAPP",
            NextAction.SEND_WHATSAPP,
        ),
        (
            "WEBSITE_CONTACT_FORM",
            "https://business.example/contact",
            "CONTACT_PUBLIC_CONTACT_FORM",
            NextAction.PREPARE_CONTACT_FORM,
        ),
    ],
)
def test_explicit_public_channels(channel: str, value: str, kind: str, action: NextAction) -> None:
    c = context()
    person = c.contacts[0]
    record = Evidence(
        company_id=c.company.id,
        source_id=c.evidence[0].source_id,
        evidence_type=kind,
        statement=value,
        raw_value={"contact_id": str(person.contact_id), "business_facing": True},
        confidence=0.9,
        observed_at=c.as_of,
        created_at=c.as_of,
    )
    contact = ContactRecommendation.model_validate(
        {
            **person.model_dump(),
            "channels": [PublicChannel(channel_type=channel, value=value, evidence_id=record.id)],
            "evidence_ids": person.evidence_ids + [record.id],
        }
    )
    item = evaluate(replace(c, contacts=(contact,), evidence=c.evidence + (record,))).item
    assert item.recommended_next_action == action and item.channel_evidence_id == record.id


def test_channel_preference_and_unproven_channel_rejected() -> None:
    c = context()
    person = c.contacts[0]
    email = Evidence(
        company_id=c.company.id,
        source_id=c.evidence[0].source_id,
        evidence_type="CONTACT_PUBLIC_EMAIL",
        statement="ana@example.com",
        raw_value={"contact_id": str(person.contact_id)},
        confidence=0.9,
        observed_at=c.as_of,
        created_at=c.as_of,
    )
    contact = ContactRecommendation.model_validate(
        {
            **person.model_dump(),
            "channels": person.channels
            + [PublicChannel(channel_type="EMAIL", value=email.statement, evidence_id=email.id)],
            "evidence_ids": person.evidence_ids + [email.id],
        }
    )
    data = replace(c, contacts=(contact,), evidence=c.evidence + (email,))
    assert evaluate(data).item.recommended_channel == "LINKEDIN"
    assert (
        evaluate(
            data, settings=ShortlistSettings(channel_preference=("EMAIL", "LINKEDIN"))
        ).item.recommended_channel
        == "EMAIL"
    )
    forged = ContactRecommendation.model_validate(
        {
            **person.model_dump(),
            "channels": [
                PublicChannel(
                    channel_type="EMAIL", value="guessed@example.com", evidence_id=uuid4()
                )
            ],
        }
    )
    item = evaluate(replace(c, contacts=(forged,))).item
    assert (
        item.recommended_channel is None
        and item.recommended_next_action == NextAction.REVIEW_MANUALLY
    )


def test_freshness_and_new_score_requires_research() -> None:
    c = context()
    older = replace(c, as_of=c.as_of + timedelta(days=31))
    decision = evaluate(older)
    assert (
        not decision.suppression_reasons
        and decision.item.recommended_next_action == NextAction.RESEARCH_MORE
    )
    assert (
        decision.item.components[-1].points == 0 and decision.item.recommended_contact_name is None
    )
    assert (
        SuppressionReason.STALE_DATA
        in evaluate(replace(c, as_of=c.as_of + timedelta(days=91))).suppression_reasons
    )
    assert c.score
    identity = uuid4()
    updated = LeadScore.model_validate(
        {
            **c.score.model_dump(),
            "id": identity,
            "calculated_at": c.as_of,
            "components": [
                {**part.model_dump(), "lead_score_id": identity} for part in c.score.components
            ],
        }
    )
    item = evaluate(replace(c, score=updated)).item
    assert (
        item.recommended_next_action == NextAction.RESEARCH_MORE and item.components[1].points == 0
    )


def test_manual_suppression_interval() -> None:
    c = context()
    record = ShortlistSuppression(
        lead_id=c.lead.id,
        starts_at=c.as_of,
        expires_at=c.as_of + timedelta(days=14),
        reason="Waiting for referral",
    )
    decision = evaluate(replace(c, suppressions=(record,)))
    assert SuppressionReason.MANUAL_SUPPRESSION in decision.suppression_reasons
    assert decision.item.eligible_again_at == record.expires_at
    assert not record.active_at(record.expires_at)
    revoked = ShortlistSuppression.model_validate({**record.model_dump(), "revoked_at": c.as_of})
    assert not revoked.active_at(c.as_of)


@pytest.mark.parametrize("data", [{"limit": 0}, {"minimum_lead_score": 101}])
def test_filter_validation(data: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        ShortlistFilters.model_validate(data)


def test_settings_validation() -> None:
    with pytest.raises(ValidationError):
        ShortlistSettings(cooldowns={InteractionType.EMAIL: 0})
    with pytest.raises(ValidationError):
        ShortlistSettings(channel_preference=("EMAIL", "EMAIL"))
    c = context()
    settings = ShortlistSettings.model_validate(
        {
            **ShortlistSettings().model_dump(),
            "cooldowns": {**ShortlistSettings().cooldowns, InteractionType.EMAIL: 14},
        }
    )
    assert (
        SuppressionReason.RECENT_CONTACT
        in evaluate(
            replace(c, interactions=(interaction(c, InteractionType.EMAIL, 10),)), settings=settings
        ).suppression_reasons
    )


def test_filters_explain_exclusion() -> None:
    c = context()
    for filters in (
        ShortlistFilters(minimum_lead_score=99),
        ShortlistFilters(pipeline_status=LeadStatus.PROPOSAL),
        ShortlistFilters(opportunity_type=OpportunityType.PRIVATE_EVENTS),
    ):
        assert evaluate(c, filters).suppression_reasons


def test_no_clear_opportunity_and_missing_brief() -> None:
    c = context()
    assert evaluate(replace(c, brief=None)).item.recommended_next_action == NextAction.RESEARCH_MORE
    assert c.brief
    empty = CommercialBrief.model_validate(
        {
            **c.brief.model_dump(),
            "primary_opportunity": None,
            "secondary_opportunities": [],
            "supporting_evidence_ids": [],
            "opportunity_type": "NO_CLEAR_OPPORTUNITY",
            "opportunity_priority": "NONE",
            "status": "INSUFFICIENT_DATA",
        }
    )
    result = evaluate(replace(c, brief=empty))
    assert SuppressionReason.NO_CLEAR_OPPORTUNITY in result.suppression_reasons
    assert SuppressionReason.INSUFFICIENT_RESEARCH in result.suppression_reasons


def test_deterministic_policy() -> None:
    c = context()
    assert evaluate(c) == evaluate(c)


def test_acceptance_roundtrip_history_and_no_mutation(engine: Engine) -> None:
    run = run_acceptance(engine)
    assert [item.shortlist_priority_score for item in run.items] == [100, 75, 64]
    assert run.policy_version == "daily-shortlist-v1" and run.candidates_considered == 6
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(DailyShortlistRun, run.id) == run
        states = {lead.id: lead for lead in uow.repository.list(Lead)}
        scores = uow.repository.list(LeadScore)
    service = DailyShortlistService(
        lambda: SqlAlchemyUnitOfWork(engine), clock=lambda: run.generated_at
    )
    again = service.today()
    assert again.items == run.items and again.suppressed == run.suppressed
    assert len(service.history()) == 2
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert {lead.id: lead for lead in uow.repository.list(Lead)} == states
        assert uow.repository.list(LeadScore) == scores


@pytest.mark.parametrize("format", list(ShortlistFormat))
def test_exports(engine: Engine, format: ShortlistFormat) -> None:
    run = run_acceptance(engine)
    text = export_runs([run], format)
    if format == ShortlistFormat.JSON:
        assert DailyShortlistRun.model_validate(json.loads(text)[0]) == run
    elif format == ShortlistFormat.CSV:
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == 3 and rows[0]["next_action"] == "SEND_LINKEDIN_CONNECTION"
    else:
        assert "DAILY SHORTLIST" in text and "MANUAL_SUPPRESSION" in text


def test_suppress_unsuppress_persistence(engine: Engine) -> None:
    run = run_acceptance(engine)
    lead_id = run.items[0].lead_id
    service = DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine))
    record = service.suppress(lead_id, 14, "Waiting")
    assert SuppressionReason.MANUAL_SUPPRESSION in service.explain(lead_id).suppression_reasons
    assert service.unsuppress(lead_id) == 1 and service.unsuppress(lead_id) == 0
    assert SuppressionReason.MANUAL_SUPPRESSION not in service.explain(lead_id).suppression_reasons
    with SqlAlchemyUnitOfWork(engine) as uow:
        persisted = uow.repository.get(ShortlistSuppression, record.id)
        assert persisted and persisted.revoked_at
    assert any(
        SuppressionReason.MANUAL_SUPPRESSION in d.suppression_reasons for d in run.suppressed
    )


def test_scoped_filters_limits_and_ties(engine: Engine) -> None:
    run = run_acceptance(engine)
    service = DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine))
    health = service.today(
        ShortlistFilters(campaign_type=CampaignType.HEALTH, city="LIMA", limit=1)
    )
    assert (
        health.items[0].lead_id == run.items[0].lead_id and len(health.eligible_not_selected) == 1
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        lead = uow.repository.get(Lead, run.items[0].lead_id)
        assert lead
    scoped = service.today(ShortlistFilters(campaign_id=lead.campaign_id))
    assert scoped.candidates_considered == 1
    assert service.today(ShortlistFilters(city="Cusco")).items_returned == 0
    # Restore the manual candidate and align its synthetic score/name to exercise stable ties.
    suppressed_id = next(
        d.item.lead_id
        for d in run.suppressed
        if SuppressionReason.MANUAL_SUPPRESSION in d.suppression_reasons
    )
    service.unsuppress(suppressed_id)
    with SqlAlchemyUnitOfWork(engine) as uow:
        tied_lead = uow.repository.get(Lead, suppressed_id)
        assert tied_lead
        company = uow.repository.get(Company, tied_lead.company_id)
        assert company
        uow.repository.update_company(
            Company.model_validate(
                {**company.model_dump(), "canonical_name": "AAA Business", "updated_at": utc_now()}
            )
        )
        uow.commit()
    items = service.today().items
    assert items[0].lead_id == suppressed_id and items[1].lead_id == run.items[0].lead_id


def test_transaction_rollback(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_acceptance(engine)
    service = DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine))
    original = SqlAlchemyRepository.add

    def reject(self: SqlAlchemyRepository, entity: Entity) -> None:
        original(self, entity)
        if isinstance(entity, (DailyShortlistRun, ShortlistSuppression)):
            raise OSError("Fixture failure after flush")

    monkeypatch.setattr(SqlAlchemyRepository, "add", reject)
    with pytest.raises(OSError):
        service.today()
    assert service.history() == [run]
    with pytest.raises(OSError):
        service.suppress(run.items[0].lead_id, 14, "Failure")
    assert (
        SuppressionReason.MANUAL_SUPPRESSION
        not in service.explain(run.items[0].lead_id).suppression_reasons
    )


def test_cli(engine: Engine, database_url: str, tmp_path: Path) -> None:
    run = run_acceptance(engine)
    runner = CliRunner()
    options = ["--database-url", database_url]
    for command, args in [
        ("today", ["--type", "HEALTH", "--limit", "5"]),
        ("explain", ["--lead-id", str(run.items[0].lead_id)]),
        ("history", ["--format", "json"]),
    ]:
        result = runner.invoke(app, ["shortlist", command, *args, *options])
        assert result.exit_code == 0, result.output
    lead_id = str(run.items[0].lead_id)
    result = runner.invoke(
        app,
        [
            "shortlist",
            "suppress",
            "--lead-id",
            lead_id,
            "--days",
            "14",
            "--reason",
            "Waiting",
            *options,
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["shortlist", "unsuppress", "--lead-id", lead_id, *options])
    assert result.exit_code == 0, result.output
    output = tmp_path / "shortlist.md"
    result = runner.invoke(
        app, ["shortlist", "today", "--format", "markdown", "--output", str(output), *options]
    )
    assert result.exit_code == 0, result.output
    assert "DAILY SHORTLIST" in output.read_text(encoding="utf-8")
    assert (
        runner.invoke(app, ["shortlist", "today", "--output", str(output), *options]).exit_code == 1
    )
    assert (
        runner.invoke(app, ["shortlist", "explain", "--lead-id", str(uuid4()), *options]).exit_code
        == 1
    )


def test_explicit_whatsapp_claim_without_phone_inference() -> None:
    c = context()
    person = c.contacts[0]
    record = Evidence(
        company_id=c.company.id,
        source_id=c.evidence[0].source_id,
        evidence_type="CONTACT_PUBLIC_WHATSAPP",
        statement="https://wa.me/51123456789",
        raw_value={"contact_id": str(person.contact_id), "business_facing": True},
        confidence=0.9,
        observed_at=c.as_of,
        created_at=c.as_of,
    )
    settings = ShortlistSettings(channel_preference=("WHATSAPP", "LINKEDIN"))
    item = evaluate(replace(c, evidence=c.evidence + (record,)), settings=settings).item
    assert (
        item.recommended_next_action == NextAction.SEND_WHATSAPP and record.id in item.evidence_ids
    )
    private = Evidence.model_validate(
        {
            **record.model_dump(),
            "raw_value": {"contact_id": str(person.contact_id), "business_facing": False},
        }
    )
    assert (
        evaluate(
            replace(c, evidence=c.evidence + (private,)), settings=settings
        ).item.recommended_channel
        == "LINKEDIN"
    )


def test_shortlist_migration_preserves_existing_briefs(database_url: str) -> None:
    from alembic import command

    from lead_engine.infrastructure.database import build_engine
    from lead_engine.infrastructure.schema import (
        database_revision,
        migration_config,
        upgrade_database,
    )

    command.upgrade(migration_config(database_url), "0005")
    engine = build_engine(database_url)
    try:
        c = context()
        assert c.brief and c.score
        source_id = c.evidence[0].source_id
        source = Source(
            id=source_id, source_type=SourceType.WEBSITE, url="https://business.example/team"
        )
        with SqlAlchemyUnitOfWork(engine) as uow:
            for entity in (
                c.company,
                c.campaign,
                c.lead,
                source,
                *c.evidence,
                *c.audits,
                c.score,
                c.brief,
            ):
                uow.repository.add(entity)
            uow.commit()
        upgrade_database(database_url)
        assert database_revision(engine) == "0007"
        command.downgrade(migration_config(database_url), "0005")
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.get(CommercialBrief, c.brief.id) == c.brief
            assert uow.repository.get(LeadScore, c.score.id) == c.score
        upgrade_database(database_url)
    finally:
        engine.dispose()


def test_future_record_and_custom_config(engine: Engine, database_url: str, tmp_path: Path) -> None:
    c = context()
    future = LeadInteraction(
        lead_id=c.lead.id,
        interaction_type=InteractionType.EMAIL,
        occurred_at=c.as_of + timedelta(days=1),
    )
    assert (
        evaluate(replace(c, interactions=(future,))).item.recommended_next_action
        == NextAction.REVIEW_MANUALLY
    )
    run_acceptance(engine)
    config = tmp_path / "settings.json"
    config.write_text(
        ShortlistSettings(channel_preference=("EMAIL", "LINKEDIN")).model_dump_json(),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        [
            "shortlist",
            "today",
            "--config",
            str(config),
            "--format",
            "json",
            "--database-url",
            database_url,
        ],
    )
    assert result.exit_code == 0, result.output
    persisted = DailyShortlistRun.model_validate(json.loads(result.stdout)[0])
    assert persisted.settings.channel_preference == ("EMAIL", "LINKEDIN")


def test_manual_public_channel_cli_and_selection(engine: Engine, database_url: str) -> None:
    run = run_acceptance(engine)
    first = run.items[0]
    assert first.recommended_contact_id
    runner = CliRunner()
    args = [
        "contact",
        "channel-add",
        "--contact-id",
        str(first.recommended_contact_id),
        "--type",
        "WHATSAPP",
        "--value",
        "https://wa.me/51123456789",
        "--source-url",
        "https://business.example/contact",
        "--database-url",
        database_url,
    ]
    assert runner.invoke(app, args).exit_code == 1
    result = runner.invoke(app, [*args, "--business-facing"])
    assert result.exit_code == 0, result.output
    claim = Evidence.model_validate_json(result.stdout)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Source, claim.source_id) is not None
        assert claim.company_id == first.company_id
    service = DailyShortlistService(
        lambda: SqlAlchemyUnitOfWork(engine),
        ShortlistSettings(channel_preference=("WHATSAPP", "LINKEDIN")),
    )
    item = service.explain(first.lead_id).item
    assert (
        item.recommended_next_action == NextAction.SEND_WHATSAPP
        and item.channel_evidence_id == claim.id
    )


def test_company_campaign_dedup_and_shared_cooldown(engine: Engine) -> None:
    from lead_engine.application.research import CommercialResearchService

    run = run_acceptance(engine)
    first = run.items[0]
    with SqlAlchemyUnitOfWork(engine) as uow:
        campaign = Campaign(name="Second campaign", campaign_type=CampaignType.HEALTH)
        lead = Lead(
            id=UUID(int=1),
            company_id=first.company_id,
            campaign_id=campaign.id,
            status=LeadStatus.QUALIFIED,
        )
        assert first.score_id is not None
        original = uow.repository.get(LeadScore, first.score_id)
        assert original
        identity = uuid4()
        score = LeadScore.model_validate(
            {
                **original.model_dump(),
                "id": identity,
                "lead_id": lead.id,
                "calculated_at": utc_now(),
                "components": [
                    {**p.model_dump(), "id": uuid4(), "lead_score_id": identity}
                    for p in original.components
                ],
            }
        )
        for entity in (campaign, lead, score):
            uow.repository.add(entity)
        uow.commit()
    CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine)).research_lead(lead.id)
    service = DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine))
    selected = service.today()
    assert selected.items[0].lead_id == lead.id
    duplicate = next(d for d in selected.suppressed if d.item.lead_id == first.lead_id)
    assert SuppressionReason.DUPLICATE_COMPANY in duplicate.suppression_reasons
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(
            LeadInteraction(
                lead_id=first.lead_id, interaction_type=InteractionType.EMAIL, occurred_at=utc_now()
            )
        )
        uow.commit()
    decision = service.explain(lead.id)
    assert SuppressionReason.RECENT_CONTACT in decision.suppression_reasons
    assert any("another campaign" in reason for reason in decision.item.reasons)
    scoped = service.today(ShortlistFilters(campaign_id=campaign.id))
    assert not scoped.items and scoped.candidates_suppressed == 1
