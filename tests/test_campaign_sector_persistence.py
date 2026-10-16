from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from pydantic import ValidationError
from sqlalchemy import inspect, text

from lead_engine.domain.enums import CampaignType
from lead_engine.domain.models import Campaign
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import database_revision, migration_config


def test_legacy_campaign_without_sector_remains_valid() -> None:
    campaign = Campaign(name="Legacy", campaign_type=CampaignType.HEALTH)
    assert campaign.sector_id is None
    assert campaign.subsector_id is None
    assert campaign.profile_id == "health-v1"


@pytest.mark.parametrize(
    ("sector_id", "subsector_id", "campaign_type", "profile_id"),
    [
        ("health", None, CampaignType.HEALTH, "health-v1"),
        ("health", "private-clinics", CampaignType.HEALTH, "health-v1"),
        (
            "construction-real-estate",
            "building-materials",
            CampaignType.CONSTRUCTION,
            "construction-v1",
        ),
        (
            "hospitality-tourism",
            "hotels",
            CampaignType.HOSPITALITY,
            "hospitality-v1",
        ),
    ],
)
def test_valid_sector_and_profile_combinations(
    sector_id: str,
    subsector_id: str | None,
    campaign_type: CampaignType,
    profile_id: str,
) -> None:
    campaign = Campaign(
        name="Classified campaign",
        campaign_type=campaign_type,
        profile_id=profile_id,
        sector_id=sector_id,
        subsector_id=subsector_id,
    )
    assert campaign.sector_id == sector_id
    assert campaign.subsector_id == subsector_id


def test_sector_classification_is_separate_from_targeting_and_profile() -> None:
    campaign = Campaign(
        name="Clinics Chiclayo",
        campaign_type=CampaignType.HEALTH,
        profile_id="health-v1",
        sector_id="health",
        subsector_id="private-clinics",
        industry="healthcare",
        subindustry="outpatient clinics",
    )
    assert campaign.sector_id == "health"
    assert campaign.subsector_id == "private-clinics"
    assert campaign.industry == "healthcare"
    assert campaign.subindustry == "outpatient clinics"
    assert campaign.profile_id == "health-v1"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"subsector_id": "private-clinics"}, "requires sector_id"),
        ({"sector_id": "unknown"}, "Unknown economic sector"),
        ({"sector_id": "health", "subsector_id": "unknown"}, "Unknown economic subsector"),
        ({"sector_id": "health", "subsector_id": "restaurants"}, "must belong to sector_id"),
        (
            {"sector_id": "health", "profile_id": "construction-v1"},
            "campaign_type|must match the sector",
        ),
        ({"sector_id": "logistics-transport"}, "no validated campaign profile yet"),
        ({"sector_id": "  "}, "must not be blank"),
        ({"subsector_id": " "}, "must not be blank"),
    ],
)
def test_invalid_sector_classification_is_rejected(values: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Campaign(name="Invalid", campaign_type=CampaignType.HEALTH, **values)


def test_campaign_sector_and_subsector_round_trip(engine) -> None:
    campaign = Campaign(
        name="Round trip",
        campaign_type=CampaignType.HEALTH,
        sector_id="health",
        subsector_id="private-clinics",
        industry="healthcare",
        subindustry="outpatient clinics",
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(campaign)
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        loaded = uow.repository.get(Campaign, campaign.id)
    assert loaded == campaign
    assert loaded is not None
    assert (loaded.sector_id, loaded.subsector_id) == ("health", "private-clinics")
    assert (loaded.industry, loaded.subindustry, loaded.profile_id) == (
        "healthcare",
        "outpatient clinics",
        "health-v1",
    )


def test_migration_adds_nullable_classification_and_downgrades_without_losing_leads(
    database_url: str,
) -> None:
    config = migration_config(database_url)
    command.upgrade(config, "0010")

    company_id, campaign_id, lead_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC).isoformat()
    engine = build_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies (id, canonical_name, created_at, updated_at) "
                    "VALUES (:id, :name, :created, :updated)"
                ),
                {
                    "id": company_id.hex,
                    "name": "Legacy company",
                    "created": now,
                    "updated": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO campaigns "
                    "(id, name, campaign_type, profile_id, active, created_at, updated_at) "
                    "VALUES (:id, :name, :type, :profile, :active, :created, :updated)"
                ),
                {
                    "id": campaign_id.hex,
                    "name": "Legacy campaign",
                    "type": "HEALTH",
                    "profile": "health-v1",
                    "active": True,
                    "created": now,
                    "updated": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO leads "
                    "(id, company_id, campaign_id, status, priority, created_at, updated_at) "
                    "VALUES (:id, :company, :campaign, :status, :priority, :created, :updated)"
                ),
                {
                    "id": lead_id.hex,
                    "company": company_id.hex,
                    "campaign": campaign_id.hex,
                    "status": "DISCOVERED",
                    "priority": 0,
                    "created": now,
                    "updated": now,
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = build_engine(database_url)
    try:
        assert database_revision(engine) == "0011"
        assert {"sector_id", "subsector_id"} <= {
            column["name"] for column in inspect(engine).get_columns("campaigns")
        }
        with engine.connect() as connection:
            migrated = connection.execute(
                text("SELECT sector_id, subsector_id FROM campaigns WHERE id = :id"),
                {"id": campaign_id.hex},
            ).one()
            assert migrated == (None, None)
            assert connection.execute(
                text("SELECT campaign_id, company_id FROM leads WHERE id = :id"),
                {"id": lead_id.hex},
            ).one() == (campaign_id.hex, company_id.hex)
    finally:
        engine.dispose()

    command.downgrade(config, "0010")
    engine = build_engine(database_url)
    try:
        assert database_revision(engine) == "0010"
        columns = {column["name"] for column in inspect(engine).get_columns("campaigns")}
        assert "sector_id" not in columns and "subsector_id" not in columns
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT campaign_type, profile_id FROM campaigns WHERE id = :id"),
                {"id": campaign_id.hex},
            ).one() == ("HEALTH", "health-v1")
            assert (
                connection.execute(
                    text("SELECT campaign_id FROM leads WHERE id = :id"), {"id": lead_id.hex}
                ).scalar_one()
                == campaign_id.hex
            )
    finally:
        engine.dispose()


def test_latest_migration_head_is_0011() -> None:
    from lead_engine.infrastructure.schema import latest_revision

    assert latest_revision() == "0011"
