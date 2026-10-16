import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from lead_engine.cli.main import app
from lead_engine.domain.campaign_profiles import get_campaign_profile
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.sectors import (
    DEFAULT_SECTORS,
    DEFAULT_SUBSECTORS,
    SUGGEST_SECTOR,
    EconomicSector,
    EconomicSubsector,
    SectorSuggestionStatus,
    build_sector_registry,
    build_subsector_registry,
    create_sector_suggestion,
    get_sector,
    get_subsector,
    subsectors_for_sector,
)


def test_catalog_contains_exactly_nine_ordered_official_sectors_without_other() -> None:
    assert tuple(DEFAULT_SECTORS) == (
        "health",
        "construction-real-estate",
        "industry-manufacturing",
        "logistics-transport",
        "commerce-distribution",
        "hospitality-tourism",
        "education",
        "b2b-services",
        "technology-digital",
    )
    assert len(DEFAULT_SECTORS) == 9
    assert len({sector.sector_id for sector in DEFAULT_SECTORS.values()}) == 9
    assert len({sector.display_order for sector in DEFAULT_SECTORS.values()}) == 9
    assert all(sector.name.casefold() != "other" for sector in DEFAULT_SECTORS.values())
    assert SUGGEST_SECTOR == "SUGGEST_SECTOR"


def test_sector_profiles_link_only_where_validated() -> None:
    assert get_sector("health").recommended_profile_id == "health-v1"
    assert get_sector("construction-real-estate").recommended_profile_id == "construction-v1"
    assert get_sector("hospitality-tourism").recommended_profile_id == "hospitality-v1"
    assert get_sector("logistics-transport").recommended_profile_id is None
    assert get_sector("industry-manufacturing").recommended_profile_id is None
    assert get_sector("commerce-distribution").recommended_profile_id is None
    assert get_sector("education").recommended_profile_id is None
    assert get_sector("b2b-services").recommended_profile_id is None
    assert get_sector("technology-digital").recommended_profile_id is None
    for sector in DEFAULT_SECTORS.values():
        if sector.recommended_profile_id is not None:
            assert get_campaign_profile(sector.recommended_profile_id).profile_id


def test_catalog_records_are_immutable_and_registries_are_read_only() -> None:
    with pytest.raises(TypeError):
        DEFAULT_SECTORS["new"] = get_sector("health")  # type: ignore[index]
    with pytest.raises(TypeError):
        DEFAULT_SUBSECTORS["new"] = get_subsector("dental")  # type: ignore[index]
    with pytest.raises(ValidationError):
        get_sector("health").name = "Changed"  # type: ignore[misc]


def test_registries_reject_duplicate_ids_display_orders_and_orphan_subsectors() -> None:
    health = get_sector("health")
    with pytest.raises(ValueError, match="Duplicate sector ID"):
        build_sector_registry((health, health))
    second = EconomicSector(
        sector_id="other-id",
        name="Another",
        description="Another sector.",
        display_order=health.display_order,
    )
    with pytest.raises(ValueError, match="Duplicate sector display order"):
        build_sector_registry((health, second))
    dental = get_subsector("dental")
    with pytest.raises(ValueError, match="Duplicate subsector ID"):
        build_subsector_registry((dental, dental), DEFAULT_SECTORS)
    orphan = EconomicSubsector(subsector_id="orphan", sector_id="missing", name="Orphan")
    with pytest.raises(ValueError, match="Unknown sector for subsector"):
        build_subsector_registry((orphan,), DEFAULT_SECTORS)


def test_unknown_sector_and_subsector_lookups_fail_clearly() -> None:
    with pytest.raises(LookupError, match="Unknown economic sector"):
        get_sector("unknown")
    with pytest.raises(LookupError, match="Unknown economic subsector"):
        get_subsector("unknown")


def test_subsectors_are_unique_linked_and_returned_in_stable_order() -> None:
    assert len(DEFAULT_SUBSECTORS) == 22
    assert len({item.subsector_id for item in DEFAULT_SUBSECTORS.values()}) == 22
    assert all(item.sector_id in DEFAULT_SECTORS for item in DEFAULT_SUBSECTORS.values())
    assert tuple(item.subsector_id for item in subsectors_for_sector("health")) == (
        "private-clinics",
        "diagnostics",
        "ophthalmology",
        "dental",
    )
    assert tuple(item.subsector_id for item in subsectors_for_sector("education")) == (
        "schools",
        "higher-education",
        "training",
    )
    with pytest.raises(LookupError, match="Unknown economic sector"):
        subsectors_for_sector("unknown")


def test_provisional_suggestion_preserves_manual_input_and_allows_no_profile() -> None:
    suggestion = create_sector_suggestion(
        requested_name="Agroexportación",
        suggested_sector_id="industry-manufacturing",
        suggested_subsector_id="agroindustry",
    )
    assert suggestion.requested_name == "Agroexportación"
    assert suggestion.suggested_sector_id == "industry-manufacturing"
    assert suggestion.suggested_subsector_id == "agroindustry"
    assert suggestion.suggested_profile_id is None
    assert suggestion.status == SectorSuggestionStatus.PROVISIONAL


def test_invalid_suggestion_sector_subsector_relationship_and_profile_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown economic sector"):
        create_sector_suggestion("Unknown", "missing-sector")
    with pytest.raises(ValidationError, match="must belong to suggested sector"):
        create_sector_suggestion("Dental lab", "health", "restaurants")
    with pytest.raises(ValidationError, match="Unknown suggested campaign profile"):
        create_sector_suggestion("Unknown profile", "health", suggested_profile_id="made-up-v1")


def test_campaign_type_enum_is_unchanged() -> None:
    assert tuple(CampaignType) == (
        CampaignType.HEALTH,
        CampaignType.CONSTRUCTION,
        CampaignType.HOSPITALITY,
    )


def test_campaign_sectors_cli_matches_catalog_in_deterministic_order() -> None:
    result = CliRunner().invoke(app, ["campaign", "sectors"])
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records == [sector.model_dump(mode="json") for sector in DEFAULT_SECTORS.values()]
    assert [record["sector_id"] for record in records] == list(DEFAULT_SECTORS)


def test_campaign_subsectors_cli_lists_known_sector_and_rejects_unknown() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["campaign", "subsectors", "--sector", "health"])
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [record["subsector_id"] for record in records] == [
        item.subsector_id for item in subsectors_for_sector("health")
    ]
    unknown = runner.invoke(app, ["campaign", "subsectors", "--sector", "missing"])
    assert unknown.exit_code != 0
    assert "Unknown economic sector" in unknown.output
