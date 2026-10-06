"""Offline acceptance fixtures: five companies and public attributed roles."""

from sqlalchemy import Engine

from lead_engine.application.contact_research import DecisionMakerResearchService
from lead_engine.domain.contact_research import ContactCandidate, RecommendationResult
from lead_engine.domain.enums import CampaignType, RoleCategory, SourceType
from lead_engine.domain.models import Campaign, Company, Lead
from lead_engine.infrastructure.contact_providers import ManualContactProvider
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork


def run_acceptance(engine: Engine) -> list[RecommendationResult]:
    service = DecisionMakerResearchService(lambda: SqlAlchemyUnitOfWork(engine))
    fixtures: list[tuple[str, CampaignType, str | None, list[tuple[str, str, RoleCategory]]]] = [
        (
            "Health Clinic",
            CampaignType.HEALTH,
            None,
            [
                ("Juan Garcia", "Medical Director", RoleCategory.MEDICAL_DIRECTOR),
                ("Ana Perez", "General Manager", RoleCategory.GENERAL_MANAGEMENT),
            ],
        ),
        (
            "Construction Distributor",
            CampaignType.CONSTRUCTION,
            None,
            [
                ("Luis Soto", "Commercial Manager", RoleCategory.COMMERCIAL),
                ("Maria Ruiz", "Owner", RoleCategory.OWNER),
            ],
        ),
        (
            "Independent Restaurant",
            CampaignType.HOSPITALITY,
            "independent",
            [
                ("Jose Leon", "Owner", RoleCategory.OWNER),
                ("Pablo Diaz", "Operations Manager", RoleCategory.ADMINISTRATION),
            ],
        ),
        (
            "Restaurant Group",
            CampaignType.HOSPITALITY,
            "group",
            [
                ("Rosa Flores", "Brand Manager", RoleCategory.MARKETING),
                ("Pedro Rojas", "Restaurant Manager", RoleCategory.GENERAL_MANAGEMENT),
            ],
        ),
        ("No Person", CampaignType.HOSPITALITY, None, []),
    ]
    results = []
    for name, kind, context, people in fixtures:
        company = Company(
            canonical_name=name, website=f"https://{name.lower().replace(' ', '-')}.example/"
        )
        campaign = Campaign(name=name, campaign_type=kind)
        lead = Lead(company_id=company.id, campaign_id=campaign.id)
        with SqlAlchemyUnitOfWork(engine) as uow:
            for entity in (company, campaign, lead):
                uow.repository.add(entity)
            uow.commit()
        candidates = [
            ContactCandidate(
                full_name=person,
                role_title=role,
                role_category=category,
                company_id=company.id,
                source_url=company.website or "https://fixture.example/",
                source_type=SourceType.WEBSITE,
                association_statement=f"{person}: {role} at {name}",
                confidence=0.9,
                context=context,
                context_statement=f"Official company description: {context}" if context else None,
            )
            for person, role, category in people
        ]
        service.research(company.id, ManualContactProvider(candidates), lead.id, force=True)
        result = service.recommend_contacts(lead.id)
        assert all(c.evidence_ids for c in result.contacts)
        results.append(result)
    assert [r.contacts[0].full_name for r in results[:4]] == [
        "Ana Perez",
        "Maria Ruiz",
        "Jose Leon",
        "Rosa Flores",
    ]
    assert results[4].target_roles and not results[4].contacts
    return results


if __name__ == "__main__":
    url = "sqlite+pysqlite:///:memory:"
    engine = build_engine()
    # Migration on the same in-memory connection.
    from alembic import command

    from lead_engine.infrastructure.schema import migration_config

    with engine.begin() as connection:
        config = migration_config(url)
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    for result in run_acceptance(engine):
        print(result.model_dump_json(indent=2))
    engine.dispose()
