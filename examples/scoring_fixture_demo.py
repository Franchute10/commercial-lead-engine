"""Offline four-lead scoring acceptance: synthetic facts, no HTTP/search/AI."""

from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import JsonValue
from sqlalchemy import Engine

from lead_engine.application.auditor import WebsiteAuditService
from lead_engine.application.scoring import CommercialScoringService
from lead_engine.application.services import LeadService
from lead_engine.application.website import FetchResult
from lead_engine.domain.enums import CampaignType, RoleCategory, SourceType
from lead_engine.domain.models import Campaign, Company, Contact, Evidence, Lead, Source, utc_now
from lead_engine.domain.scoring import ScoringResult
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import upgrade_database
from lead_engine.infrastructure.website.html_analyzer import BeautifulSoupHtmlAnalyzer


class FixtureFetcher:
    def fetch(self, url: str) -> FetchResult:
        body = (
            "<html><head><title>Synthetic business</title></head>"
            "<body><h1>Welcome</h1></body></html>"
        )
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            body=body,
            response_time_ms=50,
            body_size_bytes=len(body),
            content_type="text/html",
        )


def create_fixtures(engine: Engine) -> tuple[Lead, ...]:
    leads = []
    common: dict[str, JsonValue] = {
        "INSTAGRAM_ACTIVE": True,
        "FACEBOOK_ACTIVE": True,
        "PAID_ADVERTISING_ACTIVE": True,
        "RECENT_EXPANSION": True,
        "NEW_LOCATION": True,
        "ACTIVE_HIRING": True,
    }
    business: dict[CampaignType, dict[str, JsonValue]] = {
        CampaignType.HEALTH: {
            "HIGH_VALUE_SERVICE": True,
            "CORPORATE_CLIENTS": True,
            "MULTIPLE_LOCATIONS": True,
            "GOOGLE_REVIEW_COUNT": 500,
            "GOOGLE_RATING": "4.8",
        },
        CampaignType.CONSTRUCTION: {
            "DISTRIBUTION_NETWORK": True,
            "B2B_OPERATION": True,
            "HIGH_VALUE_PRODUCT": True,
        },
        CampaignType.HOSPITALITY: {
            "PRIVATE_EVENTS": True,
            "GOOGLE_REVIEW_COUNT": 500,
            "GOOGLE_RATING": "4.8",
        },
    }
    for kind in CampaignType:
        with SqlAlchemyUnitOfWork(engine) as uow:
            writer = LeadService(uow)
            campaign = writer.create_campaign(
                Campaign(name=f"Fixture {kind.value}", campaign_type=kind)
            )
            company = writer.upsert_company(
                Company(
                    canonical_name=f"Fixture {kind.value}",
                    website=f"http://{kind.value.lower()}.example/",
                )
            )
            contact = writer.create_contact(
                Contact(
                    company_id=company.id,
                    full_name="Synthetic Owner",
                    role_category=RoleCategory.OWNER,
                )
            )
            lead = writer.create_lead(Lead(company_id=company.id, campaign_id=campaign.id))
            source = writer.add_source(
                Source(
                    source_type=SourceType.MANUAL,
                    title="Synthetic commercial fixture",
                    metadata={"fixture": True},
                )
            )
            verified_access: dict[str, JsonValue] = {
                "contact_id": str(contact.id),
                "authority_confirmed": True,
                "reachable": True,
            }
            signals: dict[str, JsonValue] = {
                **common,
                **business[kind],
                "DECISION_MAKER_ACCESS": verified_access,
            }
            for signal, raw in signals.items():
                writer.add_evidence(
                    Evidence.model_validate(
                        {
                            "company_id": company.id,
                            "source_id": source.id,
                            "evidence_type": signal,
                            "raw_value": raw,
                            "confidence": 0.9,
                            "observed_at": utc_now(),
                            "statement": f"Synthetic observation: {signal}",
                        }
                    )
                )
            uow.commit()
            leads.append(lead)
        auditor = WebsiteAuditService(
            lambda: SqlAlchemyUnitOfWork(engine), FixtureFetcher(), BeautifulSoupHtmlAnalyzer()
        )
        auditor.audit_company(company.id)
    with SqlAlchemyUnitOfWork(engine) as uow:
        writer = LeadService(uow)
        control_company = writer.upsert_company(Company(canonical_name="Sparse control"))
        control_campaign = writer.create_campaign(
            Campaign(name="Control", campaign_type=CampaignType.HEALTH)
        )
        control = writer.create_lead(
            Lead(company_id=control_company.id, campaign_id=control_campaign.id)
        )
        uow.commit()
        leads.append(control)
    WebsiteAuditService(
        lambda: SqlAlchemyUnitOfWork(engine), FixtureFetcher(), BeautifulSoupHtmlAnalyzer()
    ).audit_company(control.company_id)
    return tuple(leads)


def run_acceptance(engine: Engine) -> tuple[ScoringResult, ...]:
    leads = create_fixtures(engine)
    scorer = CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine))
    results = tuple(scorer.score_lead(lead.id) for lead in leads)
    assert all(result.score.total_score > results[-1].score.total_score for result in results[:3])
    assert results[-1].score.total_score == 0
    for result in results:
        assert all(
            component.evidence_ids
            for component in result.score.components
            if component.points_awarded > 0
        )
        again = scorer.score_lead(result.score.lead_id)
        assert again.score.total_score == result.score.total_score
        assert len(scorer.history(result.score.lead_id)) == 2
        print(
            f"{result.company_name}: {result.score.total_score}/100; "
            f"band={result.summary.band.value}; "
            f"completeness={result.summary.completeness_percent}%; "
            f"version={result.score.scoring_version}"
        )
        for component in result.score.components:
            print(
                f"  {component.criterion}: {component.points_awarded}/{component.max_points}; "
                f"evidence_refs={len(component.evidence_ids)}"
            )
    return results


def main() -> None:
    with TemporaryDirectory(prefix="commercial-score-acceptance-") as folder:
        url = f"sqlite+pysqlite:///{Path(folder) / 'acceptance.db'}"
        upgrade_database(url)
        engine = build_engine(url)
        try:
            run_acceptance(engine)
        finally:
            engine.dispose()
    print("Acceptance passed; all history preserved during validation; temporary database removed")


if __name__ == "__main__":
    main()
