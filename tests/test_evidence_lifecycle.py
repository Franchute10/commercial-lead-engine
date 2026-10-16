"""Corrections are append-only and affect future observations only."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect
from typer.testing import CliRunner

from examples.shortlist_fixture_demo import run_acceptance
from lead_engine.application.evidence_lifecycle import EvidenceLifecycleService, current_evidence
from lead_engine.application.research import CommercialResearchService
from lead_engine.application.scoring import CommercialScoringService
from lead_engine.application.shortlist import DailyShortlistService
from lead_engine.cli.main import app
from lead_engine.domain.enums import CampaignType, SourceType
from lead_engine.domain.evidence_lifecycle import EvidenceDisposition, EvidenceDispositionKind
from lead_engine.domain.models import Campaign, Company, Evidence, Lead, LeadScore, Source, utc_now
from lead_engine.domain.policies import Observations
from lead_engine.domain.research import CommercialBrief
from lead_engine.domain.scoring import ScoringContext, SignalState
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.orm import Base
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import database_revision, migration_config, upgrade_database


def test_retraction_and_supersession_resolve_only_active_observations(engine: Engine) -> None:
    now = utc_now()
    company = Company(canonical_name="Synthetic clinic")
    campaign = Campaign(name="Synthetic health", campaign_type=CampaignType.HEALTH)
    lead = Lead(company_id=company.id, campaign_id=campaign.id)
    source = Source(source_type=SourceType.MANUAL, title="Original attributed statement")
    corrected = Source(source_type=SourceType.MANUAL, title="Corrected attributed statement")
    old = Evidence(
        company_id=company.id,
        source_id=source.id,
        evidence_type="HIGH_VALUE_SERVICE",
        statement="Original synthetic observation",
        raw_value=True,
        confidence=0.9,
        observed_at=now - timedelta(days=2),
    )
    newer = Evidence(
        company_id=company.id,
        source_id=corrected.id,
        evidence_type="HIGH_VALUE_SERVICE",
        statement="Later synthetic observation",
        raw_value=False,
        confidence=0.9,
        observed_at=now - timedelta(days=1),
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        for entity in (company, campaign, lead, source, corrected, old, newer):
            uow.repository.add(entity)
        uow.commit()

    def observed() -> SignalState:
        with SqlAlchemyUnitOfWork(engine) as uow:
            context = ScoringContext(
                lead=lead,
                company=company,
                campaign=campaign,
                evidence=current_evidence(uow.repository, company.id, utc_now()),
                contacts=(),
                audits=(),
                as_of=utc_now(),
            )
            return Observations(context).fact("HIGH_VALUE_SERVICE").state

    assert observed() == SignalState.ABSENT
    lifecycle = EvidenceLifecycleService(lambda: SqlAlchemyUnitOfWork(engine))
    withdrawn = lifecycle.dispose(
        newer.id, EvidenceDispositionKind.RETRACTED, "Frank", "Source was unsuitable"
    )
    assert observed() == SignalState.PRESENT
    assert lifecycle.history(newer.id) == (newer, [withdrawn])
    with pytest.raises(ValueError, match="already"):
        lifecycle.dispose(newer.id, EvidenceDispositionKind.RETRACTED, "Frank", "Duplicate")
    with pytest.raises(ValueError, match="same company"):
        foreign = Evidence(
            company_id=uuid4(),
            source_id=source.id,
            evidence_type="HIGH_VALUE_SERVICE",
            statement="Unrelated",
            raw_value=True,
            confidence=0.9,
            observed_at=now,
        )
        # A real second company keeps the foreign-key boundary in the test.
        with SqlAlchemyUnitOfWork(engine) as uow:
            uow.repository.add(Company(id=foreign.company_id, canonical_name="Other company"))
            uow.repository.add(foreign)
            uow.commit()
        lifecycle.dispose(
            old.id, EvidenceDispositionKind.SUPERSEDED, "Frank", "Wrong company", foreign.id
        )
    replacement = Evidence(
        company_id=company.id,
        source_id=corrected.id,
        evidence_type="HIGH_VALUE_SERVICE",
        statement="Corrected provenance confirms service",
        raw_value=True,
        confidence=0.95,
        observed_at=utc_now(),
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(replacement)
        uow.commit()
    superseded = lifecycle.dispose(
        old.id, EvidenceDispositionKind.SUPERSEDED, "Frank", "Better source", replacement.id
    )
    assert superseded.replacement_evidence_id == replacement.id
    assert observed() == SignalState.PRESENT
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Evidence, old.id) == old
        assert uow.repository.get(Evidence, newer.id) == newer
        assert {e.id for e in current_evidence(uow.repository, company.id, utc_now())} == {
            replacement.id
        }
        assert len(uow.repository.list(EvidenceDisposition, company_id=company.id)) == 2


def test_retraction_preserves_frozen_score_brief_and_shortlist(engine: Engine) -> None:
    run = run_acceptance(engine)
    item = run.items[0]
    with SqlAlchemyUnitOfWork(engine) as uow:
        score = uow.repository.get(LeadScore, item.score_id) if item.score_id else None
        brief = uow.repository.get(CommercialBrief, item.brief_id) if item.brief_id else None
        assert score and brief
        evidence_id = next(
            e.id
            for e in uow.repository.list(Evidence, company_id=item.company_id)
            if e.evidence_type == "HIGH_VALUE_SERVICE"
        )
    lifecycle = EvidenceLifecycleService(lambda: SqlAlchemyUnitOfWork(engine))
    lifecycle.dispose(evidence_id, EvidenceDispositionKind.RETRACTED, "Frank", "Withdraw source")
    ranking = DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine))
    assert ranking.explain_run(item.lead_id, run.id)[1].item == item
    context = None
    with SqlAlchemyUnitOfWork(engine) as uow:
        lead = uow.repository.get(Lead, item.lead_id)
        assert lead
        context = ranking.build_context(uow.repository, lead, utc_now())
        assert uow.repository.get(LeadScore, score.id) == score
        assert uow.repository.get(CommercialBrief, brief.id) == brief
    assert context.score is None and context.brief is None
    assert evidence_id not in {e.id for e in context.evidence}
    rescored = CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine)).score_lead(
        item.lead_id
    )
    assert evidence_id not in {
        ref for component in rescored.score.components for ref in component.evidence_ids
    }
    regenerated = CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine)).research_lead(
        item.lead_id
    )
    assert evidence_id not in regenerated.supporting_evidence_ids
    assert ranking.explain_run(item.lead_id, run.id)[1].item == item


def test_evidence_cli_and_additive_migration(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'lifecycle.db'}"
    command.upgrade(migration_config(url), "0008")
    engine = build_engine(url)
    company = Company(canonical_name="Synthetic clinic")
    source = Source(source_type=SourceType.MANUAL, title="Synthetic source")
    evidence = Evidence(
        company_id=company.id,
        source_id=source.id,
        evidence_type="HIGH_VALUE_SERVICE",
        statement="Synthetic statement",
        raw_value=True,
        confidence=0.9,
        observed_at=utc_now(),
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        for entity in (company, source, evidence):
            uow.repository.add(entity)
        uow.commit()
    upgrade_database(url)
    assert database_revision(engine) == "0011"
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    runner = CliRunner()
    options = ["--database-url", url]
    for signal in ("ADDRESS_PRESENT", "HAS_CONVERSION_CTA"):
        added = runner.invoke(
            app,
            [
                "evidence",
                "add",
                "--company-id",
                str(company.id),
                "--type",
                signal,
                "--value",
                "true",
                "--value-type",
                "boolean",
                "--source-url",
                "https://synthetic.example/observations",
                *options,
            ],
        )
        assert added.exit_code == 0, added.output
    result = runner.invoke(
        app,
        [
            "evidence",
            "retract",
            "--evidence-id",
            str(evidence.id),
            "--actor",
            "Frank",
            "--reason",
            "Unsuitable source",
            *options,
        ],
    )
    assert result.exit_code == 0, result.output
    history = runner.invoke(
        app, ["evidence", "history", "--evidence-id", str(evidence.id), *options]
    )
    assert history.exit_code == 0 and "Unsuitable source" in history.stdout
    assert (
        runner.invoke(
            app,
            [
                "evidence",
                "retract",
                "--evidence-id",
                str(evidence.id),
                "--actor",
                "Frank",
                "--reason",
                "Again",
                *options,
            ],
        ).exit_code
        == 1
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Evidence, evidence.id) == evidence
    command.downgrade(migration_config(url), "0008")
    assert "evidence_dispositions" not in inspect(engine).get_table_names()
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Evidence, evidence.id) == evidence
    engine.dispose()
