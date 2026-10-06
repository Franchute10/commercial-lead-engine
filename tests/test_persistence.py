from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text

from lead_engine.application.services import LeadService
from lead_engine.domain.enums import CampaignType, InteractionType, LeadStatus, SourceType
from lead_engine.domain.errors import (
    DuplicateError,
    IdentityConflictError,
    NotFoundError,
    RelationshipError,
)
from lead_engine.domain.models import (
    Campaign,
    Company,
    Contact,
    Evidence,
    Lead,
    LeadInteraction,
    LeadScore,
    ScoreComponent,
    Source,
)
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork


def test_full_round_trip_and_campaign_status_listing(engine: Engine) -> None:
    observed = datetime(2026, 1, 1, 9, tzinfo=timezone(timedelta(hours=-5)))
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        company = service.upsert_company(
            Company(canonical_name="Clinic", primary_domain="clinic.pe")
        )
        contact = service.create_contact(
            Contact(company_id=company.id, full_name="Ana", confidence=0.8)
        )
        source = service.add_source(
            Source(source_type=SourceType.MANUAL, metadata={"author": "human"})
        )
        evidence = service.add_evidence(
            Evidence(
                company_id=company.id,
                source_id=source.id,
                evidence_type="REVIEWS",
                statement="427 reviews",
                raw_value=427,
                confidence=0.9,
                observed_at=observed,
            )
        )
        campaign = service.create_campaign(
            Campaign(name="Health Lima", campaign_type=CampaignType.HEALTH)
        )
        lead = service.create_lead(Lead(company_id=company.id, campaign_id=campaign.id))
        score_id = uuid4()
        score = service.record_lead_score(
            LeadScore(
                id=score_id,
                lead_id=lead.id,
                total_score=Decimal("72.125"),
                scoring_version="manual-v1",
                components=(
                    ScoreComponent(
                        lead_score_id=score_id,
                        criterion="gap",
                        points_awarded=Decimal("72.125"),
                        max_points=Decimal(100),
                        explanation="Manual review",
                        evidence_ids=(evidence.id,),
                    ),
                ),
            )
        )
        interaction = service.record_interaction(
            LeadInteraction(
                lead_id=lead.id,
                contact_id=contact.id,
                interaction_type=InteractionType.NOTE,
                occurred_at=observed,
                notes="Research only",
            )
        )
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Company, company.id) == company
        assert uow.repository.get(Contact, contact.id) == contact
        assert uow.repository.get(Source, source.id) == source
        assert uow.repository.get(Evidence, evidence.id) == evidence
        assert uow.repository.get(Campaign, campaign.id) == campaign
        assert uow.repository.get(Lead, lead.id) == lead
        assert uow.repository.get(LeadScore, score.id) == score
        assert uow.repository.get(LeadInteraction, interaction.id) == interaction
        restored = uow.repository.get(Evidence, evidence.id)
        assert restored is not None and restored.observed_at.utcoffset() == timedelta(0)
        service = LeadService(uow)
        assert service.retrieve_company(company.id) == company
        assert service.list_leads(campaign.id, LeadStatus.DISCOVERED) == [lead]
        assert service.list_leads(campaign.id, LeadStatus.WON) == []


@pytest.mark.parametrize("signal", ["domain", "legal", "name"])
def test_deterministic_deduplication(engine: Engine, signal: str) -> None:
    kwargs: dict[str, object] = {
        "canonical_name": "Clínica Ágil",
        "city": "Lima",
        "country": "Perú",
    }
    if signal == "domain":
        kwargs["primary_domain"] = "WWW.CLINIC.PE"
    if signal == "legal":
        kwargs["legal_name"] = "Clínica Ágil S.A.C."
    original = Company.model_validate(kwargs)
    with SqlAlchemyUnitOfWork(engine) as uow:
        first = LeadService(uow).upsert_company(original)
        uow.commit()
    kwargs["canonical_name"] = "CLINICA AGIL" if signal == "name" else "Different display name"
    if signal == "legal":
        kwargs["legal_name"] = "CLINICA AGIL S A C"
    if signal == "domain":
        kwargs["primary_domain"] = "https://clinic.pe/"
    with SqlAlchemyUnitOfWork(engine) as uow:
        second = LeadService(uow).upsert_company(Company.model_validate(kwargs))
        assert first.id == second.id
        assert len(uow.repository.list(Company)) == 1
        uow.commit()


def test_enrich_missing_data_and_keep_known_values(engine: Engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        first = service.upsert_company(
            Company(canonical_name="Clinic", city="Lima", country="Peru")
        )
        enriched = service.upsert_company(
            Company(
                canonical_name="Clinic",
                city="Lima",
                country="Peru",
                primary_domain="clinic.pe",
                phone="123",
            )
        )
        assert enriched.id == first.id
        assert enriched.primary_domain == "clinic.pe"
        again = service.upsert_company(
            Company(canonical_name="Changed", primary_domain="clinic.pe", phone="456")
        )
        assert again.phone == "123"
        assert again.canonical_name == "Clinic"
        uow.commit()


def test_conflicting_domains_require_review(engine: Engine) -> None:
    with pytest.raises(IdentityConflictError), SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        service.upsert_company(
            Company(canonical_name="Clinic", city="Lima", country="Peru", primary_domain="one.pe")
        )
        service.upsert_company(
            Company(canonical_name="Clinic", city="Lima", country="Peru", primary_domain="two.pe")
        )


def test_conflicting_signals_require_review(engine: Engine) -> None:
    with pytest.raises(IdentityConflictError), SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        service.upsert_company(
            Company(canonical_name="One", city="Lima", country="Peru", primary_domain="one.pe")
        )
        service.upsert_company(
            Company(canonical_name="Two", city="Lima", country="Peru", primary_domain="two.pe")
        )
        service.upsert_company(
            Company(canonical_name="One", city="Lima", country="Peru", primary_domain="two.pe")
        )


def test_unknown_location_does_not_collapse_names(engine: Engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        a = service.upsert_company(Company(canonical_name="Common name"))
        b = service.upsert_company(Company(canonical_name="Common name"))
        assert a.id != b.id
        uow.commit()


def test_company_in_multiple_campaigns_and_duplicate_lead_rejected(engine: Engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        company = service.upsert_company(
            Company(canonical_name="Clinic", primary_domain="clinic.pe")
        )
        campaigns = [
            service.create_campaign(Campaign(name=kind.value, campaign_type=kind))
            for kind in (CampaignType.HEALTH, CampaignType.HOSPITALITY)
        ]
        for campaign in campaigns:
            service.create_lead(Lead(company_id=company.id, campaign_id=campaign.id))
        with pytest.raises(DuplicateError):
            service.create_lead(Lead(company_id=company.id, campaign_id=campaigns[0].id))
        assert len(uow.repository.list(Lead)) == 2
        uow.commit()


def test_relationship_validation_and_rollback(engine: Engine) -> None:
    with pytest.raises(NotFoundError), SqlAlchemyUnitOfWork(engine) as uow:
        LeadService(uow).create_contact(Contact(company_id=uuid4(), full_name="Missing parent"))
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(Company(canonical_name="Rolled back"))
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(Company) == []


def test_invalid_cross_company_evidence_and_contact(engine: Engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        first = service.upsert_company(Company(canonical_name="First"))
        second = service.upsert_company(Company(canonical_name="Second"))
        campaign = service.create_campaign(
            Campaign(name="Health", campaign_type=CampaignType.HEALTH)
        )
        lead = service.create_lead(Lead(company_id=first.id, campaign_id=campaign.id))
        contact = service.create_contact(Contact(company_id=second.id, full_name="Other"))
        source = service.add_source(Source(source_type=SourceType.MANUAL))
        evidence = service.add_evidence(
            Evidence(
                company_id=second.id,
                source_id=source.id,
                evidence_type="NOTE",
                statement="Observation",
                confidence=1,
                observed_at=datetime.now(UTC),
            )
        )
        with pytest.raises(RelationshipError):
            service.record_interaction(
                LeadInteraction(
                    lead_id=lead.id,
                    contact_id=contact.id,
                    interaction_type=InteractionType.NOTE,
                    occurred_at=datetime.now(UTC),
                )
            )
        score_id = uuid4()
        with pytest.raises(RelationshipError):
            service.record_lead_score(
                LeadScore(
                    id=score_id,
                    lead_id=lead.id,
                    total_score=Decimal(50),
                    scoring_version="v1",
                    components=(
                        ScoreComponent(
                            lead_score_id=score_id,
                            criterion="gap",
                            points_awarded=Decimal(50),
                            max_points=Decimal(100),
                            explanation="Manual",
                            evidence_ids=(evidence.id,),
                        ),
                    ),
                )
            )
        assert uow.repository.list(LeadScore) == []


def test_database_foreign_keys_and_uniqueness(engine: Engine) -> None:
    with pytest.raises(DuplicateError), SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(Contact(company_id=uuid4(), full_name="Orphan"))
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        company = service.upsert_company(Company(canonical_name="Clinic"))
        campaign = service.create_campaign(
            Campaign(name="Health", campaign_type=CampaignType.HEALTH)
        )
        service.create_lead(Lead(company_id=company.id, campaign_id=campaign.id))
        uow.commit()
    with pytest.raises(DuplicateError), SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(Lead(company_id=company.id, campaign_id=campaign.id))


def test_missing_source_rejected(engine: Engine) -> None:
    with pytest.raises(NotFoundError), SqlAlchemyUnitOfWork(engine) as uow:
        company = LeadService(uow).upsert_company(Company(canonical_name="Clinic"))
        LeadService(uow).add_evidence(
            Evidence(
                company_id=company.id,
                source_id=uuid4(),
                evidence_type="NOTE",
                statement="Unattributed",
                confidence=1,
                observed_at=datetime.now(UTC),
            )
        )


def test_schema_tables_and_foreign_keys(engine: Engine) -> None:
    expected = {
        "companies",
        "company_identities",
        "contacts",
        "sources",
        "evidence",
        "campaigns",
        "leads",
        "lead_scores",
        "score_components",
        "component_evidence",
        "lead_interactions",
        "alembic_version",
        "discovery_runs",
        "website_audits",
        "commercial_briefs",
        "contact_identities",
        "decision_maker_research_runs",
    }
    assert set(inspect(engine).get_table_names()) == expected
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_known_id_reuses_incomplete_company(engine: Engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        first = service.upsert_company(Company(canonical_name="Clinic"))
        enriched = service.upsert_company(
            Company(id=first.id, canonical_name="Clinic", phone="123")
        )
        assert enriched.id == first.id and enriched.phone == "123"
        assert len(uow.repository.list(Company)) == 1
        uow.commit()


def test_identity_unique_constraint_and_failed_transaction_rolls_back(engine: Engine) -> None:
    from lead_engine.infrastructure.orm import CompanyIdentityRow

    with SqlAlchemyUnitOfWork(engine) as uow:
        company = LeadService(uow).upsert_company(
            Company(canonical_name="First", primary_domain="one.pe")
        )
        uow.commit()
    with pytest.raises(DuplicateError), SqlAlchemyUnitOfWork(engine) as uow:
        other = LeadService(uow).upsert_company(Company(canonical_name="Second"))
        uow.repository.session.add(CompanyIdentityRow(key="domain:one.pe", company_id=other.id))
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.list(Company) == [company]


def test_multiple_score_components_and_evidence_roundtrip(engine: Engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        service = LeadService(uow)
        company = service.upsert_company(Company(canonical_name="Clinic"))
        campaign = service.create_campaign(
            Campaign(name="Health", campaign_type=CampaignType.HEALTH)
        )
        lead = service.create_lead(Lead(company_id=company.id, campaign_id=campaign.id))
        source = service.add_source(Source(source_type=SourceType.MANUAL))
        evidence = [
            service.add_evidence(
                Evidence(
                    company_id=company.id,
                    source_id=source.id,
                    statement=f"Manual finding {index}",
                    evidence_type="NOTE",
                    confidence=1,
                    observed_at=datetime.now(UTC),
                )
            )
            for index in range(2)
        ]
        score_id = uuid4()
        components = tuple(
            ScoreComponent(
                lead_score_id=score_id,
                criterion=criterion,
                points_awarded=Decimal("20.25"),
                max_points=Decimal(50),
                explanation="Manual",
                evidence_ids=tuple(item.id for item in evidence),
            )
            for criterion in ("z", "a")
        )
        score = service.record_lead_score(
            LeadScore(
                id=score_id,
                lead_id=lead.id,
                total_score=Decimal("40.5"),
                scoring_version="manual-v1",
                components=components,
            )
        )
        uow.commit()
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(LeadScore, score.id) == score


def test_service_revalidates_unvalidated_copies(engine: Engine) -> None:
    from pydantic import ValidationError

    with SqlAlchemyUnitOfWork(engine) as uow:
        with pytest.raises(ValidationError):
            LeadService(uow).upsert_company(
                Company(canonical_name="Clinic").model_copy(update={"canonical_name": ""})
            )
        assert uow.repository.list(Company) == []
