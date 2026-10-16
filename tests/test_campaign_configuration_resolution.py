from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

import lead_engine.cli.main as cli_main
from lead_engine.application.discovery import discovery_query_for_campaign
from lead_engine.domain.campaign_profiles import (
    DEFAULT_CAMPAIGN_PROFILES,
    resolve_campaign_configuration,
)
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.models import Campaign
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork


@pytest.mark.parametrize(
    ("sector_id", "profile_id", "campaign_type"),
    [
        ("health", "health-v1", CampaignType.HEALTH),
        ("construction-real-estate", "construction-v1", CampaignType.CONSTRUCTION),
        ("hospitality-tourism", "hospitality-v1", CampaignType.HOSPITALITY),
    ],
)
def test_sector_only_resolves_validated_profile_and_campaign_type(
    sector_id: str,
    profile_id: str,
    campaign_type: CampaignType,
) -> None:
    profile = resolve_campaign_configuration(sector_id, None, None)
    assert profile == DEFAULT_CAMPAIGN_PROFILES[profile_id]
    assert profile.campaign_type == campaign_type


@pytest.mark.parametrize(
    ("profile_id", "campaign_type"),
    [
        ("health-v1", None),
        (None, CampaignType.HEALTH),
        ("health-v1", CampaignType.HEALTH),
    ],
)
def test_sector_accepts_matching_explicit_profile_and_or_type(
    profile_id: str | None,
    campaign_type: CampaignType | None,
) -> None:
    assert (
        resolve_campaign_configuration("health", profile_id, campaign_type).profile_id
        == "health-v1"
    )


def test_sector_rejects_mismatched_profile_and_type() -> None:
    with pytest.raises(ValueError, match="profile.*sector"):
        resolve_campaign_configuration("health", "construction-v1", None)
    with pytest.raises(ValueError, match="must match --type"):
        resolve_campaign_configuration("health", "health-v1", CampaignType.CONSTRUCTION)
    with pytest.raises(ValueError, match="must match --type"):
        resolve_campaign_configuration("health", None, CampaignType.CONSTRUCTION)


def test_unknown_and_unprofiled_sectors_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown economic sector"):
        resolve_campaign_configuration("unknown", None, None)
    for sector_id in (
        "industry-manufacturing",
        "logistics-transport",
        "commerce-distribution",
        "education",
        "b2b-services",
        "technology-digital",
    ):
        with pytest.raises(ValueError, match="no validated campaign profile"):
            resolve_campaign_configuration(sector_id, None, None)


def test_profile_only_and_type_only_remain_supported_and_empty_input_fails() -> None:
    assert (
        resolve_campaign_configuration(None, "health-v1", None).campaign_type == CampaignType.HEALTH
    )
    assert resolve_campaign_configuration(None, None, CampaignType.HEALTH).profile_id == "health-v1"
    with pytest.raises(ValueError, match="Provide --profile or --type"):
        resolve_campaign_configuration(None, None, None)


def test_cli_resolves_sector_only_and_round_trips_configuration(
    engine: Engine,
    database_url: str,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "campaign",
            "add",
            "--name",
            "Salud Chiclayo",
            "--sector",
            "health",
            "--subsector",
            "private-clinics",
            "--industry",
            "healthcare",
            "--subindustry",
            "outpatient clinics",
            "--country",
            "Peru",
            "--region",
            "Lambayeque",
            "--city",
            "Chiclayo",
            "--database-url",
            database_url,
        ],
    )
    assert result.exit_code == 0, result.output
    construction_result = runner.invoke(
        cli_main.app,
        [
            "campaign",
            "add",
            "--name",
            "Materiales Arequipa",
            "--sector",
            "construction-real-estate",
            "--subsector",
            "building-materials",
            "--database-url",
            database_url,
        ],
    )
    assert construction_result.exit_code == 0, construction_result.output
    with SqlAlchemyUnitOfWork(engine) as uow:
        records = uow.repository.list(Campaign)
    assert len(records) == 2
    campaign = next(item for item in records if item.name == "Salud Chiclayo")
    assert (campaign.sector_id, campaign.subsector_id) == ("health", "private-clinics")
    assert (campaign.profile_id, campaign.campaign_type) == ("health-v1", CampaignType.HEALTH)
    assert (campaign.industry, campaign.subindustry) == ("healthcare", "outpatient clinics")
    query = discovery_query_for_campaign(campaign)
    assert query.industry == "healthcare"
    construction = next(item for item in records if item.name == "Materiales Arequipa")
    assert (construction.profile_id, construction.campaign_type) == (
        "construction-v1",
        CampaignType.CONSTRUCTION,
    )


def test_invalid_cli_configuration_never_opens_transaction(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened_transactions: list[str] = []

    @contextmanager
    def tracked_transaction(url: str) -> Iterator[None]:
        opened_transactions.append(url)
        yield

    monkeypatch.setattr(cli_main, "transaction", tracked_transaction)
    runner = CliRunner()
    invalid_options = (
        ["--sector", "logistics-transport"],
        ["--sector", "unknown"],
        ["--sector", "health", "--profile", "construction-v1"],
        ["--sector", "health", "--type", "CONSTRUCTION"],
        ["--subsector", "private-clinics", "--type", "HEALTH"],
    )
    for options in invalid_options:
        result = runner.invoke(
            cli_main.app,
            ["campaign", "add", "--name", "Invalid", *options, "--database-url", database_url],
        )
        assert result.exit_code != 0
    assert opened_transactions == []


def test_campaign_type_enum_remains_unchanged() -> None:
    assert tuple(CampaignType) == (
        CampaignType.HEALTH,
        CampaignType.CONSTRUCTION,
        CampaignType.HOSPITALITY,
    )
