import csv
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, inspect
from typer.testing import CliRunner

from lead_engine.application.discovery import DiscoveryCandidate, DiscoveryQuery
from lead_engine.application.scout import ScoutService
from lead_engine.application.services import LeadService
from lead_engine.cli.main import app
from lead_engine.domain.discovery import CandidateStatus, DiscoveryRun, DiscoveryStatus
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.errors import NotFoundError
from lead_engine.domain.models import Campaign, Company, Evidence, Lead, Source
from lead_engine.infrastructure.discovery.csv_provider import CsvDiscoveryProvider
from lead_engine.infrastructure.discovery.reports import export_report
from lead_engine.infrastructure.discovery.static_provider import StaticDiscoveryProvider
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import database_revision, migration_config, upgrade_database

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def campaign(engine: Engine, name: str = "Salud Chiclayo") -> Campaign:
    with SqlAlchemyUnitOfWork(engine) as uow:
        value = LeadService(uow).create_campaign(
            Campaign(name=name, campaign_type=CampaignType.HEALTH)
        )
        uow.commit()
        return value


def scout(engine: Engine) -> ScoutService:
    return ScoutService(lambda: SqlAlchemyUnitOfWork(engine))


def test_csv_utf8_optional_columns_and_line_provenance(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_text(
        'name,city,country,extra\n\n"Clínica Ágil",Chiclayo,Peru,value\n', encoding="utf-8-sig"
    )
    items = list(CsvDiscoveryProvider(path).discover(DiscoveryQuery(campaign_id=uuid4())))
    assert len(items) == 1 and items[0].name == "Clínica Ágil"
    assert items[0].website is None
    assert items[0].metadata["row_number"] == 3
    assert items[0].metadata["filename"] == "input.csv"
    assert items[0].to_company().country == "Peru"


def test_minimal_row_has_no_invented_country(tmp_path: Path) -> None:
    path = tmp_path / "minimal.csv"
    path.write_text("name\nIncomplete Clinic\n", encoding="utf-8")
    query = DiscoveryQuery(campaign_id=uuid4(), country="Peru", city="Chiclayo")
    item = list(CsvDiscoveryProvider(path).discover(query))[0]
    assert item.to_company().country is None and item.to_company().city is None


@pytest.mark.parametrize(
    "text", ["", "website\nhttps://example.com\n", "name,name\nA,B\n", "name,\nA,B\n"]
)
def test_bad_csv_headers(tmp_path: Path, text: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        list(CsvDiscoveryProvider(path).discover(DiscoveryQuery(campaign_id=uuid4())))


def test_malformed_csv_row_is_rejected_not_shifted(tmp_path: Path, engine: Engine) -> None:
    path = tmp_path / "shape.csv"
    path.write_text("name,country\nClinic,Peru,extra\nClinic\n", encoding="utf-8")
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id), CsvDiscoveryProvider(path)
    )
    assert run.counts["candidates"] == 2 and run.counts["rejected"] == 2
    assert all("number of cells" in (row.message or "") for row in run.outcomes)


def test_sample_import_counts_repeat_dedup_and_persisted_provenance(engine: Engine) -> None:
    context = campaign(engine)
    query = DiscoveryQuery(campaign_id=context.id)
    first = scout(engine).discover(query, CsvDiscoveryProvider(EXAMPLES / "health_chiclayo.csv"))
    assert first.counts == {
        "candidates": 5,
        "companies_created": 3,
        "companies_reused": 1,
        "conflicts": 0,
        "rejected": 1,
        "errors": 0,
        "leads_created": 3,
        "leads_reused": 1,
    }
    assert first.status == DiscoveryStatus.PARTIAL
    second = scout(engine).discover(query, CsvDiscoveryProvider(EXAMPLES / "health_chiclayo.csv"))
    assert second.counts["companies_created"] == 0 and second.counts["companies_reused"] == 4
    assert second.counts["leads_created"] == 0 and second.counts["leads_reused"] == 4
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert len(uow.repository.list(Company)) == 3
        assert len(uow.repository.list(Lead)) == 3
        assert len(uow.repository.list(Source)) == 10
        assert len(uow.repository.list(Evidence)) == 8
        assert uow.repository.get(DiscoveryRun, first.id) == first
        for outcome in first.outcomes:
            source = uow.repository.get(Source, outcome.source_id)
            assert source is not None and source.metadata is not None
            assert source.metadata["discovery_run_id"] == str(first.id)
            metadata = source.metadata["provider_metadata"]
            assert isinstance(metadata, dict) and metadata["row_number"] == outcome.row_number
            assert source.metadata["candidate"] is not None


def test_existing_company_multiple_sources_and_multiple_campaigns(engine: Engine) -> None:
    first, second = campaign(engine), campaign(engine, "Other campaign")
    with SqlAlchemyUnitOfWork(engine) as uow:
        existing = LeadService(uow).upsert_company(
            Company(canonical_name="Clinic", primary_domain="clinic.example")
        )
        uow.commit()
    candidates = [
        DiscoveryCandidate(
            name="Clinic",
            website="https://clinic.example",
            source_url=f"https://directory.example/{number}",
        )
        for number in range(2)
    ]
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=first.id), StaticDiscoveryProvider(candidates)
    )
    assert run.counts["companies_reused"] == 2 and run.counts["leads_created"] == 1
    another = scout(engine).discover(
        DiscoveryQuery(campaign_id=second.id), StaticDiscoveryProvider(candidates[:1])
    )
    assert another.counts["companies_reused"] == 1 and another.counts["leads_created"] == 1
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert len(uow.repository.list(Lead)) == 2
        assert len(uow.repository.list(Source)) == 3
        assert all(e.company_id == existing.id for e in uow.repository.list(Evidence))


def test_identity_and_fact_conflicts_are_reported_and_not_overwritten(engine: Engine) -> None:
    context = campaign(engine)
    with SqlAlchemyUnitOfWork(engine) as uow:
        original = LeadService(uow).upsert_company(
            Company(
                canonical_name="Clinic",
                primary_domain="one.example",
                phone="123",
                city="Chiclayo",
                country="Peru",
            )
        )
        uow.commit()
    candidates = [
        DiscoveryCandidate(
            name="Clinic", website="https://two.example", city="Chiclayo", country="Peru"
        ),
        DiscoveryCandidate(
            name="Clinic", website="https://one.example", phone="456", email="new@example.com"
        ),
    ]
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=context.id), StaticDiscoveryProvider(candidates)
    )
    assert run.counts["conflicts"] == 2 and run.counts["companies_reused"] == 0
    assert "phone" in (run.outcomes[1].message or "")
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Company, original.id) == original
        assert uow.repository.list(Lead) == [] and uow.repository.list(Evidence) == []
        assert len(uow.repository.list(Source)) == 2


def test_missing_data_enriched_and_nonconflicting_variants_reused(engine: Engine) -> None:
    context = campaign(engine)
    candidates = [
        DiscoveryCandidate(
            name="Clínica Ágil", website="https://agil.example", city="Chiclayo", country="Peru"
        ),
        DiscoveryCandidate(
            name="CLINICA AGIL",
            website="http://www.agil.example/",
            city="CHICLAYO",
            country="Perú",
            phone="123",
        ),
    ]
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=context.id), StaticDiscoveryProvider(candidates)
    )
    assert run.counts["companies_created"] == 1 and run.counts["companies_reused"] == 1
    with SqlAlchemyUnitOfWork(engine) as uow:
        companies = uow.repository.list(Company)
        assert companies[0].phone == "123" and companies[0].canonical_name == "Clínica Ágil"


def test_rejected_company_and_source_url_audited_without_business_writes(engine: Engine) -> None:
    query = DiscoveryQuery(campaign_id=campaign(engine).id)
    candidates = [
        DiscoveryCandidate(name=""),
        DiscoveryCandidate(name="Clinic", source_url="ftp://example.com"),
        DiscoveryCandidate(name="Clinic", website="ftp://example.com"),
    ]
    run = scout(engine).discover(query, StaticDiscoveryProvider(candidates))
    assert run.counts["rejected"] == 3
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(Company) == []
        assert len(uow.repository.list(Source)) == 3
        assert uow.repository.list(Evidence) == []


def test_transaction_failure_rolls_back_company_source_evidence_and_lead(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: LeadService, evidence: Evidence) -> Evidence:
        raise RuntimeError("secret provider internals")

    monkeypatch.setattr(LeadService, "add_evidence", fail)
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id),
        StaticDiscoveryProvider(
            [DiscoveryCandidate(name="Clinic", website="https://clinic.example")]
        ),
    )
    assert run.counts["errors"] == 1
    assert "secret" not in (run.outcomes[0].message or "")
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(Company) == [] and uow.repository.list(Lead) == []
        assert uow.repository.list(Evidence) == [] and len(uow.repository.list(Source)) == 1
        assert uow.repository.get(DiscoveryRun, run.id) == run


def test_unknown_campaign_does_not_start_provider_or_run(engine: Engine) -> None:
    with pytest.raises(NotFoundError):
        scout(engine).discover(DiscoveryQuery(campaign_id=uuid4()), StaticDiscoveryProvider([]))
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(DiscoveryRun) == []


def test_bad_provider_file_persists_failed_run(tmp_path: Path, engine: Engine) -> None:
    path = tmp_path / "bad.csv"
    path.write_bytes(b"name\n\xff\n")
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id), CsvDiscoveryProvider(path)
    )
    assert run.status == DiscoveryStatus.FAILED and run.counts["candidates"] == 0
    assert "UTF-8" in (run.provider_error or "")
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(DiscoveryRun, run.id) == run


def test_limit_is_respected(engine: Engine) -> None:
    query = DiscoveryQuery(campaign_id=campaign(engine).id, limit=1)
    run = scout(engine).discover(query, CsvDiscoveryProvider(EXAMPLES / "health_chiclayo.csv"))
    assert run.counts["candidates"] == 1 and run.status == DiscoveryStatus.COMPLETED


def test_static_search_fixture_and_csv_export(tmp_path: Path, engine: Engine) -> None:
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id, query_text="clinicas"),
        StaticDiscoveryProvider.from_file(EXAMPLES / "search_results.json"),
    )
    assert run.counts["companies_created"] == 1
    path = tmp_path / "report.csv"
    export_report(run, path)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["company_id"] == str(run.outcomes[0].company_id)
    assert rows[0]["run_id"] == str(run.id)
    with pytest.raises(FileExistsError):
        export_report(run, path)


def test_cli_import_summary_report_runs_and_export(
    engine: Engine, database_url: str, tmp_path: Path
) -> None:
    campaign(engine)
    runner = CliRunner()
    options = ["--database-url", database_url]
    report = tmp_path / "report.csv"
    result = runner.invoke(
        app,
        [
            "scout",
            "import-csv",
            "--campaign",
            "Salud Chiclayo",
            "--file",
            str(EXAMPLES / "health_chiclayo.csv"),
            "--report",
            str(report),
            *options,
        ],
    )
    assert result.exit_code == 2, result.output
    for text in (
        "Candidates: 5",
        "Companies created: 3",
        "Companies reused: 1",
        "Rejected: 1",
        "Leads created: 3",
        "Leads reused: 1",
    ):
        assert text in result.stdout
    assert report.exists()
    listing = runner.invoke(app, ["scout", "runs", *options])
    assert listing.exit_code == 0 and "PARTIAL" in listing.stdout
    with SqlAlchemyUnitOfWork(engine) as uow:
        run = uow.repository.list(DiscoveryRun)[0]
    result = runner.invoke(
        app, ["scout", "report", str(run.id), "--file", str(tmp_path / "again.csv"), *options]
    )
    assert result.exit_code == 0


def test_cli_static_provider_and_campaign_ambiguity(engine: Engine, database_url: str) -> None:
    campaign(engine)
    runner = CliRunner()
    options = ["--database-url", database_url]
    args = [
        "scout",
        "run",
        "--campaign",
        "Salud Chiclayo",
        "--provider",
        "static",
        "--fixture",
        str(EXAMPLES / "search_results.json"),
        "--query",
        "clinicas",
        *options,
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    campaign(engine)
    result = runner.invoke(app, args)
    assert result.exit_code == 1 and "exactly one" in result.output


def test_migration_from_previous_schema_preserves_companies(database_url: str) -> None:
    config = migration_config(database_url)
    command.upgrade(config, "0001")
    from lead_engine.infrastructure.database import build_engine

    engine = build_engine(database_url)
    try:
        with SqlAlchemyUnitOfWork(engine) as uow:
            company = LeadService(uow).upsert_company(Company(canonical_name="Keep me"))
            uow.commit()
        upgrade_database(database_url)
        assert database_revision(engine) == "0003"
        assert "discovery_runs" in inspect(engine).get_table_names()
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.get(Company, company.id) == company
        command.downgrade(config, "0001")
        assert "discovery_runs" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_eager_provider_failure_is_audited(engine: Engine) -> None:
    from collections.abc import Iterable

    class FailedProvider(StaticDiscoveryProvider):
        def discover(self, query: DiscoveryQuery) -> Iterable[DiscoveryCandidate]:
            raise RuntimeError("secret credentials")

    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id), FailedProvider([])
    )
    assert run.status == DiscoveryStatus.FAILED and run.finished_at is not None
    assert "secret" not in (run.provider_error or "")
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(DiscoveryRun, run.id) == run


def test_bad_csv_header_error_is_clear_and_persisted(tmp_path: Path, engine: Engine) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("website\nhttps://example.com\n", encoding="utf-8")
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id), CsvDiscoveryProvider(path)
    )
    assert run.status == DiscoveryStatus.FAILED
    assert "name column" in (run.provider_error or "")


def test_conflict_preserves_valid_source_url(engine: Engine) -> None:
    query = DiscoveryQuery(campaign_id=campaign(engine).id)
    run = scout(engine).discover(
        query,
        StaticDiscoveryProvider(
            [
                DiscoveryCandidate(name="", source_url="https://directory.example/record"),
            ]
        ),
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        source = uow.repository.get(Source, run.outcomes[0].source_id)
        assert source is not None and source.url == "https://directory.example/record"


def test_provider_metadata_cannot_spoof_run_provenance(engine: Engine) -> None:
    query = DiscoveryQuery(campaign_id=campaign(engine).id)
    run = scout(engine).discover(
        query,
        StaticDiscoveryProvider(
            [
                DiscoveryCandidate(name="Clinic", metadata={"discovery_run_id": "fake"}),
            ]
        ),
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        source = uow.repository.get(Source, run.outcomes[0].source_id)
        assert source is not None and source.metadata is not None
        assert source.metadata["discovery_run_id"] == str(run.id)


def test_report_formula_text_is_escaped(tmp_path: Path, engine: Engine) -> None:
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id),
        StaticDiscoveryProvider(
            [
                DiscoveryCandidate(name="=Clinic"),
            ]
        ),
    )
    path = tmp_path / "report.csv"
    export_report(run, path)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["name"] == "'=Clinic"


def test_existing_report_is_not_overwritten_and_does_not_start_run(
    tmp_path: Path,
    engine: Engine,
    database_url: str,
) -> None:
    campaign(engine)
    path = tmp_path / "report.csv"
    path.write_text("keep", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scout",
            "import-csv",
            "--campaign",
            "Salud Chiclayo",
            "--file",
            str(EXAMPLES / "health_chiclayo.csv"),
            "--report",
            str(path),
            "--database-url",
            database_url,
        ],
    )
    assert result.exit_code == 1 and path.read_text(encoding="utf-8") == "keep"
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(DiscoveryRun) == []


def test_failure_after_lead_creation_rolls_back_all_business_records(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lead_engine.infrastructure.repositories import SqlAlchemyRepository

    update = SqlAlchemyRepository.update_discovery_run

    def fail_accepted(self: SqlAlchemyRepository, run: DiscoveryRun) -> None:
        if run.outcomes and run.outcomes[-1].status == CandidateStatus.ACCEPTED:
            raise RuntimeError("Simulated write failure after company, source, evidence and lead")
        update(self, run)

    monkeypatch.setattr(SqlAlchemyRepository, "update_discovery_run", fail_accepted)
    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id),
        StaticDiscoveryProvider(
            [
                DiscoveryCandidate(name="Clinic", website="https://clinic.example"),
            ]
        ),
    )
    assert run.counts["errors"] == 1 and run.counts["leads_created"] == 0
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(Company) == []
        assert uow.repository.list(Lead) == []
        assert uow.repository.list(Evidence) == []
        assert uow.repository.find_company_identity("domain:clinic.example") is None
        assert len(uow.repository.list(Source)) == 1
        assert uow.repository.get(DiscoveryRun, run.id) == run


def test_provider_failure_retains_previously_committed_candidates(engine: Engine) -> None:
    from collections.abc import Iterable

    class InterruptedProvider(StaticDiscoveryProvider):
        def discover(self, query: DiscoveryQuery) -> Iterable[DiscoveryCandidate]:
            yield DiscoveryCandidate(name="Clinic", website="https://clinic.example")
            raise RuntimeError("Unavailable source")

    run = scout(engine).discover(
        DiscoveryQuery(campaign_id=campaign(engine).id), InterruptedProvider([])
    )
    assert run.status == DiscoveryStatus.FAILED and run.counts["candidates"] == 1
    assert run.counts["companies_created"] == 1 and run.counts["leads_created"] == 1
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert len(uow.repository.list(Company)) == 1 and len(uow.repository.list(Lead)) == 1
        assert uow.repository.get(DiscoveryRun, run.id) == run
