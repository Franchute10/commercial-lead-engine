"""Offline regression coverage for trace completeness and labelled evaluation."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from lead_engine.application.intent_discovery import IntentDiscoveryService, _parse_age
from lead_engine.application.intent_evaluation import (
    RunJournal,
    evaluate,
    label_result,
    read_events,
)
from lead_engine.cli.main import app
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.models import Campaign
from lead_engine.infrastructure.public_search import SearchHit, TavilySearchProvider
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork


def test_provider_date_option_and_complete_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        payloads.append(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "results": [
                    {
                        "url": "https://example.org/post",
                        "content": "x" * 1800,
                        "published_date": "Tue, 11 Mar 2025 17:00:00 GMT",
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    for enabled in (False, True):
        hit = TavilySearchProvider("fake-key", include_published_date=enabled).search(
            "house cleaning Sarasota", country="US", search_lang="en"
        )[0]
        assert len(hit.excerpt) == 1800
        assert _parse_age(hit.page_age) == datetime(2025, 3, 11, 17, tzinfo=UTC)
    assert "include_published_date" not in payloads[0]
    assert payloads[1]["include_published_date"] is True


@pytest.mark.parametrize(
    "value", [None, "not a date", "2100-01-01", "Tue, 11 Mar 2100 17:00:00 GMT"]
)
def test_missing_bad_future_dates_remain_unknown(value: str | None) -> None:
    assert _parse_age(value) is None


def test_full_trace_twenty_hits_and_database_duplicates(engine: Engine, tmp_path: Path) -> None:
    date = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    hits = [
        SearchHit(
            "Request",
            f"https://example.org/post/{i}",
            "Looking for house cleaning in Sarasota",
            date,
            "web",
        )
        for i in range(2)
    ]
    hits += [
        SearchHit(
            "Offers",
            f"https://example.org/post/{i}",
            "Cleaning service advertisements",
            None,
            "web",
        )
        for i in range(2, 20)
    ]

    class Provider(TavilySearchProvider):
        def search(self, query: str, **kwargs: Any) -> list[SearchHit]:
            return hits

    with SqlAlchemyUnitOfWork(engine) as uow:
        campaign = Campaign(
            name="Evaluation", campaign_type=CampaignType.HOSPITALITY, city="Sarasota", country="US"
        )
        uow.repository.add(campaign)
        uow.commit()
        journal = RunJournal(tmp_path / "run.jsonl", {"arm": "fixture"})
        records, ignored, queries = IntentDiscoveryService(uow, Provider("fake")).discover(
            campaign_id=campaign.id, service="house cleaning", observe=journal.append
        )
        uow.commit()
        journal.append({"event": "complete"})
        assert (len(records), ignored, queries) == (2, 58, 3)
        report = evaluate(journal.path)
        assert report["results"] == 60
        assert report["unique_urls"] == 20
        assert report["candidate_urls"] == 2
        assert report["precision_on_labelled_candidates"] is None
        assert report["date_coverage"] == 0.1
        assert report["lanes"]["public_web"]["results"] == 20
        assert report["lanes"]["facebook_groups"]["unique_urls"] == 20
        assert report["verified_contactable_leads"] is None
        for value, outcome in [(0, "REAL_REQUEST"), (1, "WRONG_AREA"), (2, "REAL_REQUEST")]:
            label_result(journal.path, hits[value].url, outcome)
        truth = tmp_path / "truth.json"
        truth.write_text(json.dumps([hits[0].url, hits[2].url, "https://example.org/missing"]))
        report = evaluate(journal.path, truth)
        assert report["precision_on_labelled_candidates"] == 0.5
        assert report["lanes"]["public_web"]["precision_on_labelled_candidates"] == 0.5
        assert report["retrieval_recall_on_manual_sample"] == 2 / 3
        assert report["candidate_recall_on_manual_sample"] == 1 / 3
        assert report["human_identified_missed_requests"] == [hits[2].url]
        second = RunJournal(tmp_path / "second.jsonl", {})
        IntentDiscoveryService(uow, Provider("fake")).discover(
            campaign_id=campaign.id, service="house cleaning", observe=second.append
        )
        assert len([e for e in read_events(second.path) if e["event"] == "result"]) == 60
        assert evaluate(second.path)["outcomes"]["duplicate"] == 42


def test_labels_append_no_network_and_no_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    journal = RunJournal(path, {})
    with pytest.raises(FileExistsError):
        RunJournal(path, {})
    journal.append({"event": "result", "url": "https://example.org/a", "outcome": "candidate"})
    with pytest.raises(ValueError):
        label_result(path, "https://example.org/absent", "REAL_REQUEST")
    runner = CliRunner()
    for label in ("REAL_REQUEST", "UNCLEAR"):
        result = runner.invoke(
            app,
            [
                "intent",
                "label",
                "--run-file",
                str(path),
                "--url",
                "https://example.org/a",
                "--label",
                label,
            ],
        )
        assert result.exit_code == 0, result.output
    assert len([e for e in read_events(path) if e["event"] == "label"]) == 2
    report = evaluate(path)
    assert report["status"] == "INCOMPLETE"
    assert report["precision_on_labelled_candidates"] is None
    result = runner.invoke(app, ["intent", "report", "--run-file", str(path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["database_committed"] is False


def test_country_match_cannot_qualify_out_of_city_candidate(engine: Engine) -> None:
    from lead_engine.application.intent_signals import IntentSignalService
    from lead_engine.domain.intent_signal import RequestType, SignalPlatform

    with SqlAlchemyUnitOfWork(engine) as uow:
        campaign = Campaign(
            name="Sarasota only",
            campaign_type=CampaignType.HOSPITALITY,
            city="Sarasota",
            country="US",
        )
        uow.repository.add(campaign)
        record = IntentSignalService(uow).capture(
            campaign_id=campaign.id,
            platform=SignalPlatform.PUBLIC_WEB,
            source_url="https://example.org/request",
            request_type=RequestType.SERVICE_REQUEST,
            service_requested="house cleaning",
            evidence_excerpt="Looking for house cleaning in US",
            country="US",
            posted_at=datetime.now(UTC) - timedelta(days=1),
        )
        assert record.qualified_candidate is False
        assert "location not confirmed" in record.score_explanation


def test_failed_second_query_preserves_first_query_evidence(engine: Engine, tmp_path: Path) -> None:
    class Provider(TavilySearchProvider):
        calls = 0

        def search(self, query: str, **kwargs: Any) -> list[SearchHit]:
            self.calls += 1
            if self.calls == 2:
                raise OSError("Search unavailable")
            return [SearchHit("Ad", "https://example.org/ad", "Cleaning offers", None, "web")]

    journal = RunJournal(tmp_path / "failed.jsonl", {})
    with SqlAlchemyUnitOfWork(engine) as uow:
        campaign = Campaign(name="Failure", campaign_type=CampaignType.HOSPITALITY, city="Sarasota")
        uow.repository.add(campaign)
        with pytest.raises(OSError):
            IntentDiscoveryService(uow, Provider("fake")).discover(
                campaign_id=campaign.id, service="house cleaning", observe=journal.append
            )
    report = evaluate(journal.path)
    assert report["results"] == 1
    assert report["queries_attempted"] == 2
    assert report["status"] == "INCOMPLETE"


def test_cli_discover_commits_journal_and_passes_date_option(
    engine: Engine, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "offline-fixture-key")
    payloads: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        payloads.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={"results": []})

    monkeypatch.setattr(httpx, "post", post)
    with SqlAlchemyUnitOfWork(engine) as uow:
        campaign = Campaign(
            name="CLI test", campaign_type=CampaignType.HOSPITALITY, city="Sarasota"
        )
        uow.repository.add(campaign)
        uow.commit()
    path = tmp_path / "cli.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "intent",
            "discover",
            "--campaign",
            str(campaign.id),
            "--service",
            "house cleaning",
            "--include-published-date",
            "--run-file",
            str(path),
            "--database-url",
            database_url,
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(payloads) == 3
    assert all(p["include_published_date"] is True for p in payloads)
    assert evaluate(path)["database_committed"] is True
    assert evaluate(path)["results"] == 0
    assert "offline-fixture-key" not in path.read_text()


def test_campaign_migration_preserves_signals_and_refuses_orphans(database_url: str) -> None:
    from alembic import command
    from sqlalchemy import inspect, text

    from lead_engine.application.intent_signals import IntentSignalService
    from lead_engine.domain.intent_signal import IntentSignal, RequestType, SignalPlatform
    from lead_engine.infrastructure.database import build_engine
    from lead_engine.infrastructure.schema import migration_config, upgrade_database

    config = migration_config(database_url)
    command.upgrade(config, "0013")
    engine = build_engine(database_url)
    try:
        with SqlAlchemyUnitOfWork(engine) as uow:
            campaign = Campaign(name="Preserved", campaign_type=CampaignType.HOSPITALITY)
            uow.repository.add(campaign)
            signal = IntentSignalService(uow).capture(
                campaign_id=campaign.id,
                platform=SignalPlatform.PUBLIC_WEB,
                source_url="https://example.org/keep",
                request_type=RequestType.SERVICE_REQUEST,
                service_requested="cleaning",
                evidence_excerpt="Looking for cleaning",
            )
            uow.commit()
        upgrade_database(database_url)
        columns = {c["name"]: c for c in inspect(engine).get_columns("intent_signals")}
        assert columns["campaign_id"]["nullable"] is False
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.get(IntentSignal, signal.id) == signal
        command.downgrade(config, "0013")
        with engine.begin() as connection:
            connection.execute(text("UPDATE intent_signals SET campaign_id = NULL"))
        with pytest.raises(ValueError, match="without a campaign"):
            upgrade_database(database_url)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM intent_signals")) == 1
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0013"
    finally:
        engine.dispose()
