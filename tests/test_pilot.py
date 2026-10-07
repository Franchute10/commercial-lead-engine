"""Only pilot feedback, report, template and additive migration behavior; all offline."""

import csv
import io
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy import Engine, inspect
from typer.testing import CliRunner

from examples.outreach_fixture_demo import create_outreach_fixtures
from lead_engine.application.discovery import DiscoveryQuery
from lead_engine.application.outreach import OutreachDraftService
from lead_engine.application.pilot import PilotEvaluationService
from lead_engine.application.pilot_export import PilotFormat, export_report
from lead_engine.application.shortlist import DailyShortlistService
from lead_engine.cli.main import app
from lead_engine.domain.enums import CampaignType, SourceType
from lead_engine.domain.errors import DuplicateError, NotFoundError
from lead_engine.domain.models import (
    Campaign,
    Company,
    Evidence,
    Lead,
    LeadInteraction,
    LeadScore,
    ScoreComponent,
    Source,
    utc_now,
)
from lead_engine.domain.outreach import OutreachChannel, OutreachDraft, OutreachEvent
from lead_engine.domain.pilot import (
    ContactDecision,
    DecisionMakerQuality,
    OutputQuality,
    PilotEvaluation,
)
from lead_engine.domain.research import CommercialBrief, OpportunityPriority, OpportunityType
from lead_engine.domain.shortlist import (
    DailyShortlistRun,
    NextAction,
    PriorityComponent,
    ShortlistDecision,
    ShortlistFilters,
    ShortlistItem,
    ShortlistSettings,
    SuppressionReason,
)
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.discovery.csv_provider import CsvDiscoveryProvider
from lead_engine.infrastructure.orm import Base
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import migration_config, upgrade_database


@dataclass(frozen=True)
class PilotFixture:
    campaign: Campaign
    leads: tuple[Lead, ...]
    run: DailyShortlistRun
    unselected: Lead


@pytest.fixture
def cohort(engine: Engine) -> PilotFixture:
    """Persist a synthetic frozen cohort to isolate feedback from discovery/scoring tests."""
    campaign = Campaign(name="Synthetic pilot cohort", campaign_type=CampaignType.HEALTH)
    leads = []
    items = []
    with SqlAlchemyUnitOfWork(engine) as uow:
        repo = uow.repository
        repo.add(campaign)
        for index in range(4):
            company = Company(canonical_name=f"Synthetic pilot company {index}")
            lead = Lead(company_id=company.id, campaign_id=campaign.id)
            repo.add(company)
            repo.add(lead)
            if index == 3:
                unselected = lead
                continue
            score_id = uuid4()
            # Construct a consistent one-component fixture score.
            score = LeadScore(
                lead_id=lead.id,
                total_score=Decimal(70),
                scoring_version="fixture",
                id=score_id,
                components=(
                    ScoreComponent(
                        lead_score_id=score_id,
                        criterion="Fixture",
                        explanation="Synthetic pilot test score",
                        points_awarded=Decimal(70),
                        max_points=Decimal(100),
                    ),
                ),
            )
            repo.add(score)
            leads.append(lead)
            items.append(
                ShortlistItem(
                    lead_id=lead.id,
                    company_id=company.id,
                    company_name=company.canonical_name,
                    campaign_name=campaign.name,
                    campaign_type=campaign.campaign_type,
                    latest_score=70,
                    score_id=score_id,
                    score_band=None,
                    score_completeness=None,
                    brief_id=None,
                    research_status=None,
                    research_completeness=0,
                    primary_opportunity=OpportunityType.NO_CLEAR_OPPORTUNITY,
                    opportunity_priority=OpportunityPriority.NONE,
                    recommended_contact_id=None,
                    recommended_contact_name=None,
                    recommended_contact_role=None,
                    recommended_contact_fit=None,
                    recommended_channel=None,
                    recommended_channel_value=None,
                    channel_evidence_id=None,
                    target_roles=(),
                    suggested_contact_angle="Synthetic fixture only",
                    pipeline_status=lead.status,
                    last_interaction_at=None,
                    last_interaction_id=None,
                    eligible_again_at=None,
                    recommended_next_action=NextAction.RESEARCH_MORE,
                    shortlist_priority_score=50,
                    shortlist_rank=index + 1,
                    components=(
                        PriorityComponent(
                            dimension="Fixture",
                            points=50,
                            maximum_points=100,
                            explanation="Synthetic review snapshot",
                        ),
                    ),
                    warnings=(),
                    reasons=(),
                    evidence_ids=(),
                )
            )
        run = DailyShortlistRun(
            generated_at=utc_now(),
            policy_version="daily-shortlist-v1",
            filters=ShortlistFilters(campaign_id=campaign.id, limit=3),
            settings=ShortlistSettings(),
            candidates_considered=3,
            candidates_suppressed=0,
            items_returned=3,
            items=tuple(items),
            suppressed=(),
            eligible_not_selected=(),
        )
        repo.add(run)
        uow.commit()
    return PilotFixture(campaign, tuple(leads), run, unselected)


@pytest.fixture
def pilot(engine: Engine) -> PilotEvaluationService:
    return PilotEvaluationService(lambda: SqlAlchemyUnitOfWork(engine))


def evaluate(
    pilot: PilotEvaluationService,
    cohort: PilotFixture,
    index: int = 0,
    decision: ContactDecision = ContactDecision.YES,
    evaluator: str = "Frank",
) -> PilotEvaluation:
    return pilot.evaluate(
        cohort.leads[index].id,
        decision,
        DecisionMakerQuality.GOOD,
        OutputQuality.GOOD,
        OutputQuality.PARTIAL,
        "Human review",
        evaluator,
        cohort.run.id,
    )


def test_persisted_labels_and_history(
    pilot: PilotEvaluationService, cohort: PilotFixture, engine: Engine
) -> None:
    first = evaluate(pilot, cohort)
    second = evaluate(pilot, cohort, decision=ContactDecision.NO)
    assert first.id != second.id and (first.revision, second.revision) == (1, 2)
    assert pilot.history(cohort.leads[0].id) == [second, first]
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(PilotEvaluation, first.id) == first
    report = pilot.report(cohort.campaign.id, shortlist_run_id=cohort.run.id)
    assert report.companies_evaluated == 1
    assert report.would_contact_distribution[ContactDecision.NO] == 1
    assert report.shortlist_precision_percent == 0
    assert report.rows[0].evaluation == second


def test_report_denominators_and_false_positives(
    pilot: PilotEvaluationService, cohort: PilotFixture
) -> None:
    for index, decision in enumerate(ContactDecision):
        evaluate(pilot, cohort, index, decision)
    report = pilot.report(cohort.campaign.id)
    assert report.companies_evaluated == 3 and report.shortlisted_companies == 3
    assert report.precision_denominator == 2 and report.shortlist_precision_percent == 50
    assert report.would_contact_percent == 33.33
    assert report.false_positive_lead_ids == (cohort.leads[1].id,)
    assert report.decision_maker_quality_distribution[DecisionMakerQuality.GOOD] == 3
    assert report.opportunity_quality_distribution[OutputQuality.GOOD] == 3
    assert report.outreach_quality_distribution[OutputQuality.PARTIAL] == 3
    assert not report.unevaluated_lead_ids


def test_unevaluated_and_all_maybe_are_not_negative(
    pilot: PilotEvaluationService, cohort: PilotFixture
) -> None:
    empty = pilot.report(cohort.campaign.id)
    assert empty.companies_evaluated == 0 and empty.shortlist_precision_percent is None
    assert empty.would_contact_percent is None and len(empty.unevaluated_lead_ids) == 3
    evaluate(pilot, cohort, decision=ContactDecision.MAYBE)
    report = pilot.report(cohort.campaign.id)
    assert report.shortlist_precision_percent is None and report.precision_denominator == 0
    assert report.would_contact_percent == 0 and not report.false_positive_lead_ids
    assert len(report.unevaluated_lead_ids) == 2


def test_different_evaluators_are_not_mixed(
    pilot: PilotEvaluationService, cohort: PilotFixture
) -> None:
    frank = evaluate(pilot, cohort)
    other = evaluate(pilot, cohort, decision=ContactDecision.NO, evaluator="Other")
    assert frank.revision == other.revision == 1
    assert pilot.report(cohort.campaign.id).would_contact_percent == 100
    assert pilot.report(cohort.campaign.id, "Other").would_contact_percent == 0


def test_selected_cohort_and_run_identity(
    pilot: PilotEvaluationService, cohort: PilotFixture, engine: Engine
) -> None:
    with pytest.raises(ValueError, match="not selected"):
        pilot.evaluate(
            cohort.unselected.id,
            ContactDecision.NO,
            DecisionMakerQuality.UNKNOWN,
            OutputQuality.WRONG,
            OutputQuality.WRONG,
            shortlist_run_id=cohort.run.id,
        )
    with pytest.raises(NotFoundError):
        pilot.evaluate(
            uuid4(),
            ContactDecision.NO,
            DecisionMakerQuality.WRONG,
            OutputQuality.WRONG,
            OutputQuality.WRONG,
        )
    with pytest.raises(NotFoundError):
        pilot.report(uuid4())
    with pytest.raises(NotFoundError):
        pilot.report(cohort.campaign.id, shortlist_run_id=uuid4())
    with SqlAlchemyUnitOfWork(engine) as uow:
        other = Campaign(name="Other synthetic campaign", campaign_type=CampaignType.HEALTH)
        uow.repository.add(other)
        uow.commit()
    with pytest.raises(ValueError, match="matching"):
        pilot.report(other.id, shortlist_run_id=cohort.run.id)


def test_new_run_does_not_reuse_old_labels(
    pilot: PilotEvaluationService, cohort: PilotFixture, engine: Engine
) -> None:
    evaluate(pilot, cohort)
    new_run = DailyShortlistRun.model_validate(
        {**cohort.run.model_dump(), "id": uuid4(), "generated_at": utc_now()}
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(new_run)
        uow.commit()
    assert pilot.report(cohort.campaign.id).companies_evaluated == 0
    assert pilot.report(cohort.campaign.id, shortlist_run_id=cohort.run.id).companies_evaluated == 1


def test_empty_shortlist_report(
    pilot: PilotEvaluationService, cohort: PilotFixture, engine: Engine
) -> None:
    run = DailyShortlistRun.model_validate(
        {
            **cohort.run.model_dump(),
            "id": uuid4(),
            "generated_at": utc_now(),
            "candidates_considered": 0,
            "items_returned": 0,
            "items": (),
        }
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(run)
        uow.commit()
    report = pilot.report(cohort.campaign.id)
    assert report.shortlisted_companies == 0 and report.shortlist_precision_percent is None
    data = list(csv.DictReader(io.StringIO(export_report(report, PilotFormat.CSV))))
    assert len(data) == 1 and data[0]["companies_evaluated"] == "0"


def test_future_cutoff_and_naive_clock(cohort: PilotFixture, engine: Engine) -> None:
    old = PilotEvaluationService(
        lambda: SqlAlchemyUnitOfWork(engine),
        clock=lambda: cohort.run.generated_at - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="non-future"):
        old.report(cohort.campaign.id)
    naive = PilotEvaluationService(
        lambda: SqlAlchemyUnitOfWork(engine), clock=lambda: utc_now().replace(tzinfo=None)
    )
    with pytest.raises(ValueError, match="aware"):
        naive.report(cohort.campaign.id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("would_contact", "UNKNOWN"),
        ("decision_maker_quality", "EXCELLENT"),
        ("opportunity_quality", "UNKNOWN"),
        ("outreach_quality", "UNKNOWN"),
        ("evaluator", " "),
        ("revision", 0),
    ],
)
def test_label_validation(
    pilot: PilotEvaluationService, cohort: PilotFixture, field: str, value: object
) -> None:
    record = evaluate(pilot, cohort)
    with pytest.raises(ValidationError):
        PilotEvaluation.model_validate({**record.model_dump(), field: value})


def test_pilot_never_mutates_core_records(
    pilot: PilotEvaluationService, cohort: PilotFixture, engine: Engine
) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        scores = uow.repository.list(LeadScore)
        leads = uow.repository.list(Lead)
        run = uow.repository.get(DailyShortlistRun, cohort.run.id)
    evaluate(pilot, cohort, decision=ContactDecision.NO)
    pilot.report(cohort.campaign.id)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(LeadScore) == scores and uow.repository.list(Lead) == leads
        assert uow.repository.get(DailyShortlistRun, cohort.run.id) == run
        assert not uow.repository.list(Evidence) and not uow.repository.list(LeadInteraction)


def test_failed_commit_and_stale_revision_roll_back(
    pilot: PilotEvaluationService,
    cohort: PilotFixture,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = evaluate(pilot, cohort)
    with pytest.raises(DuplicateError):
        with SqlAlchemyUnitOfWork(engine) as uow:
            uow.repository.add(
                PilotEvaluation.model_validate({**first.model_dump(), "id": uuid4()})
            )
            uow.commit()

    def fail(self: SqlAlchemyUnitOfWork) -> None:
        raise ValueError("Synthetic commit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail)
    with pytest.raises(ValueError):
        evaluate(pilot, cohort, decision=ContactDecision.NO)
    assert pilot.history(first.lead_id) == [first]


def test_frozen_artifact_links_and_draft_identity(engine: Engine) -> None:
    ids = create_outreach_fixtures(engine)
    with SqlAlchemyUnitOfWork(engine) as uow:
        lead = uow.repository.get(Lead, ids[0])
        assert lead is not None
    shortlist = DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine))
    run = shortlist.today(ShortlistFilters(campaign_id=lead.campaign_id))
    writer = OutreachDraftService(lambda: SqlAlchemyUnitOfWork(engine))
    draft = writer.draft(lead.id, OutreachChannel.EMAIL).draft
    other = writer.draft(ids[1], OutreachChannel.EMAIL).draft
    pilot = PilotEvaluationService(lambda: SqlAlchemyUnitOfWork(engine))
    with pytest.raises(ValueError, match="does not match"):
        pilot.evaluate(
            lead.id,
            ContactDecision.YES,
            DecisionMakerQuality.GOOD,
            OutputQuality.GOOD,
            OutputQuality.GOOD,
            shortlist_run_id=run.id,
            outreach_draft_id=other.id,
        )
    with SqlAlchemyUnitOfWork(engine) as uow:
        scores = uow.repository.list(LeadScore)
        briefs = uow.repository.list(CommercialBrief)
        evidence = uow.repository.list(Evidence)
        drafts = uow.repository.list(OutreachDraft)
    record = pilot.evaluate(
        lead.id,
        ContactDecision.YES,
        DecisionMakerQuality.GOOD,
        OutputQuality.GOOD,
        OutputQuality.PARTIAL,
        shortlist_run_id=run.id,
        outreach_draft_id=draft.id,
    )
    assert (
        record.score_id == run.items[0].score_id
        and record.commercial_brief_id == draft.commercial_brief_id
    )
    assert (
        record.outreach_draft_id == draft.id and record.recommended_contact_id == draft.contact_id
    )
    writer.draft(lead.id, OutreachChannel.EMAIL, force=True)
    report = pilot.report(lead.campaign_id, shortlist_run_id=run.id)
    assert report.rows[0].evaluation == record
    assert not report.rows[0].needs_more_research
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert (
            uow.repository.list(LeadScore) == scores
            and uow.repository.list(CommercialBrief) == briefs
        )
        assert uow.repository.list(Evidence) == evidence
        assert uow.repository.get(OutreachDraft, draft.id) == next(
            d for d in drafts if d.id == draft.id
        )
        # Pilot records do not approve or claim use of any draft.
        assert not [e for e in uow.repository.list(OutreachEvent) if e.actor == "HUMAN"]


@pytest.mark.parametrize("format", list(PilotFormat))
def test_report_exports(
    pilot: PilotEvaluationService, cohort: PilotFixture, format: PilotFormat
) -> None:
    record = evaluate(pilot, cohort)
    report = pilot.report(cohort.campaign.id)
    text = export_report(report, format)
    assert str(record.lead_id) in text
    if format == PilotFormat.JSON:
        data = json.loads(text)
        assert data["companies_evaluated"] == 1
        assert data["rows"][0]["evaluation"]["id"] == str(record.id)
    elif format == PilotFormat.CSV:
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == 3 and rows[0]["evaluation_id"] == str(record.id)
        assert rows[1]["would_contact"] == "" and rows[0]["precision_denominator"] == "1"
    else:
        assert "YES / YES+NO" in text and "Decision-maker quality" in text and "UNEVALUATED" in text


def test_export_untrusted_notes_are_safe(
    pilot: PilotEvaluationService, cohort: PilotFixture
) -> None:
    record = pilot.evaluate(
        cohort.leads[0].id,
        ContactDecision.MAYBE,
        DecisionMakerQuality.UNKNOWN,
        OutputQuality.PARTIAL,
        OutputQuality.WRONG,
        notes='=HYPERLINK("https://example.invalid")\n<img src=x>',
        shortlist_run_id=cohort.run.id,
    )
    report = pilot.report(cohort.campaign.id)
    markdown = export_report(report, PilotFormat.MARKDOWN)
    assert "<img" not in markdown and "&lt;img" in markdown
    rows = list(csv.DictReader(io.StringIO(export_report(report, PilotFormat.CSV))))
    assert rows[0]["evaluator_notes"].startswith("'=HYPERLINK")
    assert report.rows[0].evaluation == record


def test_csv_template_is_header_only_and_supported() -> None:
    template = Path("examples/pilot_companies_template.csv")
    with template.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 1 and "name" in rows[0] and "source_url" in rows[0]
    assert not list(CsvDiscoveryProvider(template).discover(DiscoveryQuery(campaign_id=uuid4())))


def test_cli_evaluation_report_history_and_export(
    cohort: PilotFixture, engine: Engine, tmp_path: Path
) -> None:
    runner = CliRunner()
    database = ["--database-url", str(engine.url)]
    result = runner.invoke(
        app,
        [
            "pilot",
            "evaluate",
            "--lead-id",
            str(cohort.leads[0].id),
            "--shortlist-run-id",
            str(cohort.run.id),
            "--would-contact",
            "YES",
            "--decision-maker",
            "GOOD",
            "--opportunity",
            "GOOD",
            "--outreach",
            "PARTIAL",
            "--notes",
            "Refine appointment context",
            *database,
        ],
    )
    assert result.exit_code == 0, result.output
    record_id = json.loads(result.stdout)["id"]
    for format in PilotFormat:
        output = tmp_path / f"report.{format}"
        result = runner.invoke(
            app,
            [
                "pilot",
                "report",
                "--campaign",
                cohort.campaign.name,
                "--shortlist-run-id",
                str(cohort.run.id),
                "--format",
                format.value,
                "--output",
                str(output),
                *database,
            ],
        )
        assert result.exit_code == 0, result.output
        assert record_id in output.read_text(encoding="utf-8")
        assert (
            runner.invoke(
                app,
                [
                    "pilot",
                    "report",
                    "--campaign",
                    str(cohort.campaign.id),
                    "--output",
                    str(output),
                    *database,
                ],
            ).exit_code
            == 1
        )
    history = runner.invoke(
        app, ["pilot", "history", "--lead-id", str(cohort.leads[0].id), *database]
    )
    assert history.exit_code == 0 and record_id in history.stdout
    assert runner.invoke(app, ["pilot", "--help"]).exit_code == 0


def test_additive_pilot_migration_preserves_existing_data(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'pilot-migration.db'}"
    engine = build_engine(url)
    with engine.begin() as connection:
        config = migration_config(url)
        config.attributes["connection"] = connection
        command.upgrade(config, "0007")
    source = Source(source_type=SourceType.MANUAL, title="Pre-pilot source")
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(source)
        uow.commit()
    upgrade_database(url)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Source, source.id) == source
    with engine.connect() as connection:
        assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    with engine.begin() as connection:
        config = migration_config(url)
        config.attributes["connection"] = connection
        command.downgrade(config, "0007")
    assert "pilot_evaluations" not in inspect(engine).get_table_names()
    upgrade_database(url)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Source, source.id) == source
    engine.dispose()


def test_suppressed_lead_excluded_from_evaluation(
    pilot: PilotEvaluationService, cohort: PilotFixture, engine: Engine
) -> None:
    item = ShortlistItem.model_validate(
        {
            **cohort.run.items[0].model_dump(),
            "lead_id": cohort.unselected.id,
            "company_id": cohort.unselected.company_id,
            "shortlist_rank": None,
            "recommended_next_action": NextAction.NO_ACTION,
        }
    )
    run = DailyShortlistRun.model_validate(
        {
            **cohort.run.model_dump(),
            "id": uuid4(),
            "generated_at": utc_now(),
            "candidates_considered": 4,
            "candidates_suppressed": 1,
            "suppressed": (
                ShortlistDecision(item=item, suppression_reasons=(SuppressionReason.LOW_SCORE,)),
            ),
        }
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(run)
        uow.commit()
    with pytest.raises(ValueError, match="not selected"):
        pilot.evaluate(
            cohort.unselected.id,
            ContactDecision.NO,
            DecisionMakerQuality.UNKNOWN,
            OutputQuality.WRONG,
            OutputQuality.WRONG,
            shortlist_run_id=run.id,
        )
    assert pilot.report(cohort.campaign.id, shortlist_run_id=run.id).shortlisted_companies == 3


def test_future_evaluation_not_reported(
    pilot: PilotEvaluationService, cohort: PilotFixture, engine: Engine
) -> None:
    record = evaluate(pilot, cohort)
    future = PilotEvaluation.model_validate(
        {
            **record.model_dump(),
            "id": uuid4(),
            "revision": 2,
            "evaluated_at": utc_now() + timedelta(days=1),
            "would_contact": ContactDecision.NO,
        }
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(future)
        uow.commit()
    assert pilot.report(cohort.campaign.id).would_contact_percent == 100
    with pytest.raises(ValueError, match="precedes"):
        evaluate(pilot, cohort)
