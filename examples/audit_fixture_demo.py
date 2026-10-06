"""Reproduce WebsiteAuditor acceptance offline using the real adapter and MockTransport."""

from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from lead_engine.application.auditor import WebsiteAuditService
from lead_engine.application.services import LeadService
from lead_engine.application.website import AuditSettings
from lead_engine.domain.audit import FindingType, WebsiteAudit
from lead_engine.domain.models import Company, Evidence, Source
from lead_engine.infrastructure.database import build_engine
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.schema import upgrade_database
from lead_engine.infrastructure.website.html_analyzer import BeautifulSoupHtmlAnalyzer
from lead_engine.infrastructure.website.http_fetcher import HttpxFetcher
from lead_engine.infrastructure.website.security import PublicUrlPolicy


def main() -> None:
    fixture_dir = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "website"
    pages = {
        f"{name}.example": (fixture_dir / f"{name}.html").read_text(encoding="utf-8")
        for name in ("booking", "contact", "catalog")
    }
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, text=pages[request.headers["Host"]], headers={"Content-Type": "text/html"}
        )

    with TemporaryDirectory(prefix="website-audit-acceptance-") as folder:
        url = f"sqlite+pysqlite:///{Path(folder) / 'acceptance.db'}"
        upgrade_database(url)
        engine = build_engine(url)
        settings = AuditSettings(min_request_interval_seconds=0)
        fetcher = HttpxFetcher(
            settings,
            policy=PublicUrlPolicy(lambda host, port: ["93.184.216.34"]),
            transport=httpx.MockTransport(handler),
        )
        try:
            with SqlAlchemyUnitOfWork(engine) as uow:
                companies = [
                    LeadService(uow).upsert_company(
                        Company(
                            canonical_name=f"Demo {name}",
                            website=f"https://{name}.example/" if name != "none" else None,
                        )
                    )
                    for name in ("booking", "contact", "catalog", "none")
                ]
                uow.commit()
            service = WebsiteAuditService(
                lambda: SqlAlchemyUnitOfWork(engine), fetcher, BeautifulSoupHtmlAnalyzer(), settings
            )
            expected = (
                FindingType.BOOKING_CTA_PRESENT,
                FindingType.WHATSAPP_LINK_PRESENT,
                FindingType.CATALOG_PRESENT,
                FindingType.NO_WEBSITE,
            )
            for company, kind in zip(companies, expected, strict=True):
                result = service.audit_company(company.id)
                assert kind in {finding.kind for finding in result.findings}
                with SqlAlchemyUnitOfWork(engine) as uow:
                    evidence = uow.repository.list(Evidence, website_audit_id=result.audit.id)
                    assert len(evidence) == result.evidence_created and len(evidence) > 0
                    assert all(item.source_id == result.audit.source_id for item in evidence)
                    assert uow.repository.get(Source, result.audit.source_id) is not None
                print(
                    f"{company.canonical_name}: {result.audit.status.value}; "
                    f"evidence={result.evidence_created}; expected={kind.value}"
                )
            with SqlAlchemyUnitOfWork(engine) as uow:
                print(
                    f"Persisted: audits={len(uow.repository.list(WebsiteAudit))}; "
                    f"sources={len(uow.repository.list(Source))}; "
                    f"evidence={len(uow.repository.list(Evidence))}"
                )
            assert len(requests) == 6
            print("Requests: 6 mocked (robots + homepage per configured URL); no actual network")
        finally:
            fetcher.close()
            engine.dispose()
    print("Acceptance passed; temporary database removed")


if __name__ == "__main__":
    main()
