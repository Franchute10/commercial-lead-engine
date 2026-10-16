from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text

from lead_engine.domain.enums import CampaignType
from lead_engine.domain.models import Campaign, Lead
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import database_revision, migration_config


@pytest.mark.parametrize(
    ("campaign_type", "profile_id"),
    [
        (CampaignType.HEALTH, "health-v1"),
        (CampaignType.CONSTRUCTION, "construction-v1"),
        (CampaignType.HOSPITALITY, "hospitality-v1"),
    ],
)
def test_0009_campaigns_backfill_profile_and_preserve_lead(
    engine: Engine,
    database_url: str,
    campaign_type: CampaignType,
    profile_id: str,
) -> None:
    command.downgrade(migration_config(database_url), "0009")
    campaign_id, company_id, lead_id = uuid4(), uuid4(), uuid4()
    timestamp = datetime.now(UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies (id, canonical_name, created_at, updated_at) "
                "VALUES (:id, :name, :created, :updated)"
            ),
            {
                "id": company_id.hex,
                "name": "Legacy company",
                "created": timestamp,
                "updated": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO campaigns "
                "(id, name, campaign_type, description, geography, active, created_at, updated_at) "
                "VALUES (:id, :name, :kind, NULL, :geography, 1, :created, :updated)"
            ),
            {
                "id": campaign_id.hex,
                "name": "Legacy campaign",
                "kind": campaign_type.value,
                "geography": "Chiclayo",
                "created": timestamp,
                "updated": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO leads "
                "(id, company_id, campaign_id, status, priority, "
                "assigned_at, created_at, updated_at) "
                "VALUES (:id, :company_id, :campaign_id, 'DISCOVERED', 0, NULL, :created, :updated)"
            ),
            {
                "id": lead_id.hex,
                "company_id": company_id.hex,
                "campaign_id": campaign_id.hex,
                "created": timestamp,
                "updated": timestamp,
            },
        )

    command.upgrade(migration_config(database_url), "head")
    assert database_revision(engine) == "0011"
    with SqlAlchemyUnitOfWork(engine) as uow:
        campaign = uow.repository.get(Campaign, campaign_id)
        lead = uow.repository.get(Lead, lead_id)
    assert campaign is not None
    assert campaign.id == campaign_id
    assert campaign.campaign_type == campaign_type
    assert campaign.profile_id == profile_id
    assert campaign.geography == "Chiclayo"
    assert campaign.sector_id is None
    assert campaign.subsector_id is None
    assert (
        campaign.country,
        campaign.region,
        campaign.city,
        campaign.industry,
        campaign.subindustry,
    ) == (None, None, None, None, None)
    campaign_columns = {column["name"] for column in inspect(engine).get_columns("campaigns")}
    assert {
        "profile_id",
        "country",
        "region",
        "city",
        "industry",
        "subindustry",
        "sector_id",
        "subsector_id",
    } <= campaign_columns
    assert lead is not None and lead.id == lead_id and lead.campaign_id == campaign_id

    command.downgrade(migration_config(database_url), "0009")
    assert database_revision(engine) == "0009"
    downgraded_columns = {column["name"] for column in inspect(engine).get_columns("campaigns")}
    assert (
        not {
            "profile_id",
            "country",
            "region",
            "city",
            "industry",
            "subindustry",
        }
        & downgraded_columns
    )
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT geography FROM campaigns WHERE id = :id"), {"id": campaign_id.hex}
            )
            == "Chiclayo"
        )
        assert (
            connection.scalar(
                text("SELECT campaign_id FROM leads WHERE id = :id"), {"id": lead_id.hex}
            )
            == campaign_id.hex
        )
