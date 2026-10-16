"""Persistent sector suggestion intake and human review tests."""

import json
from uuid import UUID

import pytest
from alembic import command
from pydantic import ValidationError
from sqlalchemy import inspect, text
from typer.testing import CliRunner

from lead_engine.application.sector_suggestions import SectorSuggestionService
from lead_engine.cli.main import app
from lead_engine.domain.sector_suggestions import SectorSuggestionRecord, SectorSuggestionStatus
from lead_engine.domain.sectors import DEFAULT_SECTORS, SectorSuggestion, create_sector_suggestion
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import (
    database_revision,
    migration_config,
    upgrade_database,
)


def test_unmapped_and_manually_mapped_inputs_preserve_name_without_profile_inference() -> None:
    unmapped = create_sector_suggestion("  Seguridad electrónica  ")
    mapped = create_sector_suggestion("Agroexportación", "industry-manufacturing", "agroindustry")
    assert unmapped.requested_name == "  Seguridad electrónica  "
    assert unmapped.suggested_sector_id is None
    assert unmapped.suggested_subsector_id is None
    assert unmapped.suggested_profile_id is None
    assert unmapped.status is SectorSuggestionStatus.PROVISIONAL
    assert mapped.suggested_sector_id == "industry-manufacturing"
    assert mapped.suggested_profile_id is None
    assert SectorSuggestionRecord(**unmapped.model_dump()).requested_name == unmapped.requested_name


@pytest.mark.parametrize(
    "values",
    [
        {"requested_name": "  "},
        {"requested_name": "x", "suggested_sector_id": "unknown"},
        {"requested_name": "x", "suggested_subsector_id": "dental"},
        {"requested_name": "x", "suggested_sector_id": "health", "suggested_subsector_id": "bad"},
        {
            "requested_name": "x",
            "suggested_sector_id": "health",
            "suggested_subsector_id": "restaurants",
        },
        {"requested_name": "x", "suggested_profile_id": "not-a-profile"},
    ],
)
def test_invalid_suggestion_combinations_are_rejected(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        SectorSuggestion(**values)


def test_sector_catalog_is_not_mutated() -> None:
    before = tuple(DEFAULT_SECTORS)
    create_sector_suggestion("Nueva actividad")
    assert tuple(DEFAULT_SECTORS) == before


def test_changed_sector_clears_stale_mapping_and_preserves_review_context(engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        submitted = service.submit("Seguridad clínica", "health", "private-clinics", "health-v1")
        uow.commit()

    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        reviewed = service.mark_needs_review(
            submitted.id, "b2b-services", review_note="Revisar clasificación"
        )
        assert reviewed.status is SectorSuggestionStatus.NEEDS_REVIEW
        assert reviewed.suggested_sector_id == "b2b-services"
        assert reviewed.suggested_subsector_id is None
        assert reviewed.suggested_profile_id is None
        assert reviewed.review_note == "Revisar clasificación"
        assert reviewed.reviewed_at is None
        uow.commit()

    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        accepted = service.accept(submitted.id)
        assert accepted.status is SectorSuggestionStatus.ACCEPTED
        assert accepted.suggested_sector_id == "b2b-services"
        assert accepted.suggested_subsector_id is None
        assert accepted.suggested_profile_id is None
        assert accepted.review_note == "Revisar clasificación"
        assert accepted.reviewed_at is not None
        uow.commit()

    with SqlAlchemyUnitOfWork(engine) as uow:
        stored = SectorSuggestionService(uow).get(submitted.id)
        assert stored == accepted
        with pytest.raises(ValueError, match="Terminal"):
            SectorSuggestionService(uow).mark_needs_review(submitted.id)


def test_explicit_new_review_note_replaces_prior_note(engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        submitted = service.submit(
            "Seguridad empresarial", "health", suggested_profile_id="health-v1"
        )
        reviewed = service.mark_needs_review(submitted.id, review_note="Nota original")
        assert reviewed.review_note == "Nota original"
        accepted = service.accept(submitted.id, "b2b-services", review_note="Nota de aceptación")
        assert accepted.suggested_profile_id is None
        assert accepted.review_note == "Nota de aceptación"
        assert accepted.reviewed_at is not None
        uow.commit()


def test_lifecycle_and_round_trip(engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        provisional = service.submit("  Seguridad electrónica  ")
        uow.commit()
    assert isinstance(provisional.id, UUID)
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        stored = service.get(provisional.id)
        assert stored == provisional
        reviewed = service.mark_needs_review(
            provisional.id, "b2b-services", review_note="Revisar alcance"
        )
        assert reviewed.status is SectorSuggestionStatus.NEEDS_REVIEW
        assert reviewed.reviewed_at is None
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        accepted = SectorSuggestionService(uow).accept(provisional.id, review_note="Aceptado")
        assert accepted.status is SectorSuggestionStatus.ACCEPTED
        assert accepted.suggested_sector_id == "b2b-services"
        assert accepted.suggested_profile_id is None
        assert accepted.reviewed_at is not None
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        with pytest.raises(ValueError, match="Terminal"):
            service.reject(provisional.id, "No")
        assert service.get(provisional.id).status is SectorSuggestionStatus.ACCEPTED

    reject_id: UUID
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        rejected = service.submit("Actividad descartada")
        reject_id = rejected.id
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        with pytest.raises(ValueError, match="mapping"):
            service.accept(reject_id)
        with pytest.raises(ValueError, match="nonblank"):
            service.reject(reject_id, "   ")
        assert service.get(reject_id).status is SectorSuggestionStatus.PROVISIONAL
        rejected = service.reject(reject_id, "No es una categoría diferenciada")
        assert rejected.status is SectorSuggestionStatus.REJECTED
        assert rejected.reviewed_at is not None
        uow.commit()

    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        direct_accept = service.submit("Servicios de seguridad")
        pending_reject = service.submit("Actividad sin encaje")
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = SectorSuggestionService(uow)
        accepted_directly = service.accept(direct_accept.id, "b2b-services")
        assert accepted_directly.reviewed_at is not None
        service.mark_needs_review(pending_reject.id)
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        rejected_after_review = SectorSuggestionService(uow).reject(
            pending_reject.id, "No hay categoría diferenciada"
        )
        assert rejected_after_review.status is SectorSuggestionStatus.REJECTED
        assert rejected_after_review.reviewed_at is not None
        uow.commit()


def test_cli_submit_list_show_review_accept_and_reject(database_url: str) -> None:
    upgrade_database(database_url)
    runner = CliRunner()
    options = ["--database-url", database_url]
    submitted = runner.invoke(
        app,
        ["sector-suggestion", "submit", "--name", "Seguridad electrónica", *options],
    )
    assert submitted.exit_code == 0, submitted.output
    item = json.loads(submitted.stdout)
    suggestion_id = item["id"]
    assert item["suggested_sector_id"] is None
    assert item["status"] == "PROVISIONAL"

    listed = runner.invoke(app, ["sector-suggestion", "list", *options])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout.splitlines()[0])["id"] == suggestion_id
    shown = runner.invoke(app, ["sector-suggestion", "show", "--id", suggestion_id, *options])
    assert shown.exit_code == 0, shown.output
    reviewed = runner.invoke(
        app,
        [
            "sector-suggestion",
            "review",
            "--id",
            suggestion_id,
            "--sector",
            "b2b-services",
            "--note",
            "Clasificación revisada",
            *options,
        ],
    )
    assert reviewed.exit_code == 0, reviewed.output
    accepted = runner.invoke(
        app,
        [
            "sector-suggestion",
            "accept",
            "--id",
            suggestion_id,
            *options,
        ],
    )
    assert accepted.exit_code == 0, accepted.output
    accepted_item = json.loads(accepted.stdout)
    assert accepted_item["status"] == "ACCEPTED"
    assert accepted_item["suggested_profile_id"] is None
    assert accepted_item["review_note"] == "Clasificación revisada"

    rejected_submit = runner.invoke(
        app,
        ["sector-suggestion", "submit", "--name", "Actividad a descartar", *options],
    )
    rejected_id = json.loads(rejected_submit.stdout)["id"]
    rejected = runner.invoke(
        app,
        [
            "sector-suggestion",
            "reject",
            "--id",
            rejected_id,
            "--note",
            "No corresponde",
            *options,
        ],
    )
    assert rejected.exit_code == 0, rejected.output
    assert json.loads(rejected.stdout)["status"] == "REJECTED"

    terminal = runner.invoke(
        app,
        ["sector-suggestion", "review", "--id", suggestion_id, *options],
    )
    assert terminal.exit_code != 0
    assert "Terminal" in terminal.output


def test_migration_is_additive_and_downgrade_preserves_existing_records(
    database_url: str,
) -> None:
    engine = build_engine(database_url)
    try:
        command.upgrade(migration_config(database_url), "0011")
        now = "2026-10-16 12:00:00"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies (id, canonical_name, created_at, updated_at) "
                    "VALUES (:id, :name, :now, :now)"
                ),
                {"id": "11111111111111111111111111111111", "name": "Preserved", "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO campaigns "
                    "(id, name, campaign_type, profile_id, active, created_at, updated_at) "
                    "VALUES (:id, :name, 'HEALTH', 'health-v1', 1, :now, :now)"
                ),
                {"id": "22222222222222222222222222222222", "name": "Campaign", "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO leads "
                    "(id, company_id, campaign_id, status, priority, created_at, updated_at) "
                    "VALUES (:id, :company, :campaign, 'DISCOVERED', 0, :now, :now)"
                ),
                {
                    "id": "33333333333333333333333333333333",
                    "company": "11111111111111111111111111111111",
                    "campaign": "22222222222222222222222222222222",
                    "now": now,
                },
            )
        command.upgrade(migration_config(database_url), "head")
        assert database_revision(engine) == "0012"
        assert "sector_suggestions" in inspect(engine).get_table_names()
        assert "campaigns" in inspect(engine).get_table_names()
        command.downgrade(migration_config(database_url), "0011")
        assert database_revision(engine) == "0011"
        assert "sector_suggestions" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT canonical_name FROM companies")
            ).scalar_one() == ("Preserved")
            assert connection.execute(text("SELECT name FROM campaigns")).scalar_one() == "Campaign"
            assert connection.execute(text("SELECT priority FROM leads")).scalar_one() == 0
    finally:
        engine.dispose()
