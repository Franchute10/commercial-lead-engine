"""Provider-independent HTTP/HTML contracts and configurable bounded audit policy."""

from dataclasses import dataclass, field
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from lead_engine.domain.audit import Finding, WebsiteAudit


class AuditSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    freshness_days: Annotated[int, Field(ge=0, le=365)] = 7
    timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 10
    total_fetch_seconds: Annotated[float, Field(gt=0, le=120)] = 30
    max_response_bytes: Annotated[int, Field(ge=1024, le=5000000)] = 1000000
    max_redirects: Annotated[int, Field(ge=0, le=5)] = 3
    retry_count: Annotated[int, Field(ge=0, le=1)] = 1
    min_request_interval_seconds: Annotated[float, Field(ge=0, le=60)] = 1
    slow_response_ms: Annotated[float, Field(gt=0)] = 3000
    campaign_limit: Annotated[int, Field(ge=1, le=100)] = 5
    user_agent: str = (
        "CommercialLeadEngine/0.1 (+https://github.com/Franchute10/commercial-lead-engine)"
    )


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str | None = None
    http_status: int | None = None
    response_time_ms: float | None = None
    body: str = ""
    body_size_bytes: int = 0
    content_type: str = ""
    redirects: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class HtmlAnalysis:
    title: str | None = None
    findings: tuple[Finding, ...] = ()
    warnings: tuple[str, ...] = ()
    access_blocked: bool = False


class HttpFetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


class HtmlAnalyzer(Protocol):
    def analyze(self, html: str, page_url: str) -> HtmlAnalysis: ...


@dataclass(frozen=True)
class WebsiteAuditResult:
    audit: WebsiteAudit
    reachable: bool
    findings: tuple[Finding, ...]
    evidence_created: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    skipped_fresh: bool = False

    @property
    def company_id(self) -> UUID:
        return self.audit.company_id

    @property
    def website(self) -> str | None:
        return self.audit.website_url

    @property
    def http_status(self) -> int | None:
        return self.audit.http_status

    @property
    def final_url(self) -> str | None:
        return self.audit.final_url

    @property
    def response_time_ms(self) -> float | None:
        return self.audit.response_time_ms


@dataclass(frozen=True)
class CampaignAuditResult:
    results: tuple[WebsiteAuditResult, ...]
    skipped_no_website: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
