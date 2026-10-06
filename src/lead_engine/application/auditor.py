"""Homepage audit orchestration; HTTP and parsing remain behind ports."""

from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from lead_engine.application.discovery import UnitOfWorkFactory
from lead_engine.application.services import LeadService
from lead_engine.application.website import (
    AuditSettings,
    CampaignAuditResult,
    FetchResult,
    HtmlAnalysis,
    HtmlAnalyzer,
    HttpFetcher,
    WebsiteAuditResult,
)
from lead_engine.domain.audit import AuditStatus, Finding, FindingType, WebsiteAudit
from lead_engine.domain.enums import SourceType
from lead_engine.domain.errors import NotFoundError
from lead_engine.domain.models import Campaign, Company, Evidence, Lead, Source, utc_now


class WebsiteAuditService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        fetcher: HttpFetcher,
        analyzer: HtmlAnalyzer,
        settings: AuditSettings | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._fetcher = fetcher
        self._analyzer = analyzer
        self.settings = settings or AuditSettings()

    def audit_company(self, company_id: UUID, *, force: bool = False) -> WebsiteAuditResult:
        with self._uow_factory() as uow:
            company = uow.repository.get(Company, company_id)
            if company is None:
                raise NotFoundError("Company does not exist")
            fresh = [
                audit
                for audit in uow.repository.list(WebsiteAudit, company_id=company.id)
                if audit.status == AuditStatus.SUCCESS
                and audit.website_url == company.website
                and audit.finished_at is not None
                and audit.finished_at >= utc_now() - timedelta(days=self.settings.freshness_days)
            ]
            if fresh and not force and self.settings.freshness_days > 0:
                audit = max(fresh, key=lambda value: value.finished_at or value.started_at)
                evidence = uow.repository.list(Evidence, website_audit_id=audit.id)
                prior_findings = tuple(
                    Finding(
                        id=item.id,
                        kind=FindingType(item.evidence_type),
                        statement=item.statement,
                        value=item.raw_value,
                        confidence=item.confidence,
                    )
                    for item in evidence
                )
                return WebsiteAuditResult(
                    audit=audit,
                    reachable=True,
                    findings=prior_findings,
                    evidence_created=0,
                    warnings=audit.warnings,
                    skipped_fresh=True,
                )
        started = utc_now()
        audit_id = uuid4()
        analysis = HtmlAnalysis()
        findings: list[Finding] = []
        if company.website is None:
            fetched = FetchResult(requested_url="")
            status = AuditStatus.NO_WEBSITE
            findings.append(
                Finding(
                    kind=FindingType.NO_WEBSITE,
                    statement="No website URL is stored on the company record",
                    value=True,
                )
            )
            reachable = False
        else:
            try:
                fetched = self._fetcher.fetch(company.website)
            except Exception:
                fetched = FetchResult(
                    requested_url=company.website,
                    error_code="FETCHER_ERROR",
                    error_message="HTTP adapter failed",
                )
            reachable = (
                fetched.error_code is None
                and fetched.http_status is not None
                and 200 <= fetched.http_status < 300
            )
            if fetched.http_status is not None:
                findings.append(
                    Finding(
                        kind=FindingType.HTTP_STATUS,
                        statement="Observed HTTP response status",
                        value=fetched.http_status,
                    )
                )
            if fetched.redirects:
                findings.append(
                    Finding(
                        kind=FindingType.REDIRECT_PRESENT,
                        statement="HTTP redirect destinations were observed",
                        value=list(fetched.redirects),
                    )
                )
            if reachable:
                findings.append(
                    Finding(
                        kind=FindingType.WEBSITE_REACHABLE,
                        statement="The requested page returned a successful HTTP status",
                        value=True,
                    )
                )
                https = urlsplit(fetched.final_url or company.website).scheme == "https"
                findings.append(
                    Finding(
                        kind=FindingType.HTTPS_ENABLED if https else FindingType.HTTPS_MISSING,
                        statement="Observed final URL transport scheme",
                        value="https" if https else "http",
                    )
                )
                if (
                    fetched.response_time_ms is not None
                    and fetched.response_time_ms >= self.settings.slow_response_ms
                ):
                    findings.append(
                        Finding(
                            kind=FindingType.SLOW_RESPONSE,
                            statement="Page request elapsed time met the configured threshold",
                            value={
                                "response_time_ms": fetched.response_time_ms,
                                "threshold_ms": self.settings.slow_response_ms,
                            },
                        )
                    )
                if fetched.content_type.split(";", 1)[0].lower() in {
                    "text/html",
                    "application/xhtml+xml",
                }:
                    try:
                        analysis = self._analyzer.analyze(
                            fetched.body, fetched.final_url or company.website
                        )
                    except Exception:
                        analysis = HtmlAnalysis(
                            warnings=("HTML analysis failed; no page signals inferred",)
                        )
                else:
                    analysis = HtmlAnalysis(
                        warnings=("Response is not declared HTML; signals not parsed",)
                    )
                if analysis.access_blocked:
                    fetched = FetchResult(
                        **{
                            **fetched.__dict__,
                            "error_code": "ACCESS_BLOCKED",
                            "error_message": "Login/challenge access page detected; "
                            "no commercial signals analyzed",
                        }
                    )
                    reachable = False
                    status = AuditStatus.FAILED
                else:
                    findings.extend(analysis.findings)
                    status = (
                        AuditStatus.PARTIAL
                        if analysis.warnings or fetched.warnings
                        else AuditStatus.SUCCESS
                    )
            else:
                status = AuditStatus.FAILED
            if fetched.error_code or not reachable:
                findings.append(
                    Finding(
                        kind=FindingType.FETCH_ERROR,
                        statement="Audit could not inspect the requested public page",
                        value={
                            "error_code": fetched.error_code or "HTTP_ERROR",
                            "http_status": fetched.http_status,
                        },
                    )
                )
        observed = utc_now()
        warnings = (*fetched.warnings, *analysis.warnings)
        source = Source(
            source_type=SourceType.WEBSITE if company.website else SourceType.MANUAL,
            url=company.website,
            title=analysis.title[:1000] if analysis.title else None,
            retrieved_at=observed,
            metadata={
                "website_audit_id": str(audit_id),
                "requested_url": company.website,
                "final_url": fetched.final_url,
                "http_status": fetched.http_status,
                "response_time_ms": fetched.response_time_ms,
                "body_size_bytes": fetched.body_size_bytes,
                "redirects": list(fetched.redirects),
                "scope": "homepage_only",
                "html_text_sha256": sha256(fetched.body.encode("utf-8")).hexdigest()
                if fetched.body
                else None,
                "error_code": fetched.error_code,
                "no_website_basis": "company_record" if not company.website else None,
            },
        )
        audit = WebsiteAudit(
            id=audit_id,
            company_id=company.id,
            source_id=source.id,
            website_url=company.website,
            final_url=fetched.final_url,
            started_at=started,
            finished_at=observed,
            status=status,
            http_status=fetched.http_status,
            response_time_ms=fetched.response_time_ms,
            body_size_bytes=fetched.body_size_bytes,
            error_code=fetched.error_code
            or ("HTTP_ERROR" if status == AuditStatus.FAILED else None),
            error_message=fetched.error_message,
            warnings=warnings,
        )
        with self._uow_factory() as uow:
            service = LeadService(uow)
            service.add_source(source)
            uow.repository.add(audit)
            for finding in findings:
                service.add_evidence(
                    Evidence(
                        id=finding.id,
                        company_id=company.id,
                        source_id=source.id,
                        website_audit_id=audit.id,
                        evidence_type=finding.kind.value,
                        statement=finding.statement,
                        raw_value=finding.value,
                        confidence=finding.confidence,
                        observed_at=observed,
                    )
                )
            uow.commit()
        return WebsiteAuditResult(
            audit=audit,
            reachable=reachable,
            findings=tuple(findings),
            evidence_created=len(findings),
            errors=(audit.error_code,) if audit.error_code else (),
            warnings=warnings,
        )

    def audit_campaign(
        self, campaign_id: UUID, *, limit: int | None = None, force: bool = False
    ) -> CampaignAuditResult:
        limit = self.settings.campaign_limit if limit is None else limit
        if not 1 <= limit <= 100:
            raise ValueError("Campaign audit limit must be between 1 and 100")
        with self._uow_factory() as uow:
            if uow.repository.get(Campaign, campaign_id) is None:
                raise NotFoundError("Campaign does not exist")
            company_ids = sorted(
                {lead.company_id for lead in uow.repository.list(Lead, campaign_id=campaign_id)}
            )
            companies = [uow.repository.get(Company, value) for value in company_ids]
        skipped = sum(company is not None and not company.website for company in companies)
        results: list[WebsiteAuditResult] = []
        errors: list[str] = []
        fetched_count = 0
        for company in companies:
            if company is None or not company.website:
                continue
            if fetched_count >= limit:
                break
            try:
                result = self.audit_company(company.id, force=force)
                results.append(result)
                if not result.skipped_fresh:
                    fetched_count += 1
            except Exception:
                errors.append(f"Audit persistence failed for company {company.id}")
                fetched_count += 1
        return CampaignAuditResult(
            results=tuple(results), skipped_no_website=skipped, errors=tuple(errors)
        )
