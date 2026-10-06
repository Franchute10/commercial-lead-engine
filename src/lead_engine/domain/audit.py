"""Objective website observations and persisted audit records, without scores/severity."""

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from lead_engine.domain.models import Entity, Text, utc_now


class AuditStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    NO_WEBSITE = "NO_WEBSITE"


class FindingType(StrEnum):
    WEBSITE_SIGNAL_COVERAGE = "WEBSITE_SIGNAL_COVERAGE"
    WEBSITE_REACHABLE = "WEBSITE_REACHABLE"
    HTTP_STATUS = "HTTP_STATUS"
    HTTPS_ENABLED = "HTTPS_ENABLED"
    HTTPS_MISSING = "HTTPS_MISSING"
    MOBILE_VIEWPORT_PRESENT = "MOBILE_VIEWPORT_PRESENT"
    WHATSAPP_LINK_PRESENT = "WHATSAPP_LINK_PRESENT"
    PHONE_LINK_PRESENT = "PHONE_LINK_PRESENT"
    EMAIL_LINK_PRESENT = "EMAIL_LINK_PRESENT"
    CONTACT_FORM_PRESENT = "CONTACT_FORM_PRESENT"
    BOOKING_CTA_PRESENT = "BOOKING_CTA_PRESENT"
    QUOTE_CTA_PRESENT = "QUOTE_CTA_PRESENT"
    ECOMMERCE_PRESENT = "ECOMMERCE_PRESENT"
    CATALOG_PRESENT = "CATALOG_PRESENT"
    SOCIAL_LINK_PRESENT = "SOCIAL_LINK_PRESENT"
    INSTAGRAM_LINK_PRESENT = "INSTAGRAM_LINK_PRESENT"
    FACEBOOK_LINK_PRESENT = "FACEBOOK_LINK_PRESENT"
    LINKEDIN_LINK_PRESENT = "LINKEDIN_LINK_PRESENT"
    MAP_LINK_PRESENT = "MAP_LINK_PRESENT"
    ADDRESS_PRESENT = "ADDRESS_PRESENT"
    BUSINESS_HOURS_PRESENT = "BUSINESS_HOURS_PRESENT"
    PRIVACY_POLICY_PRESENT = "PRIVACY_POLICY_PRESENT"
    SERVICES_PAGE_PRESENT = "SERVICES_PAGE_PRESENT"
    PRODUCTS_PAGE_PRESENT = "PRODUCTS_PAGE_PRESENT"
    RESERVATION_PROVIDER_PRESENT = "RESERVATION_PROVIDER_PRESENT"
    PAGE_TITLE_PRESENT = "PAGE_TITLE_PRESENT"
    META_DESCRIPTION_PRESENT = "META_DESCRIPTION_PRESENT"
    H1_PRESENT = "H1_PRESENT"
    MULTIPLE_H1 = "MULTIPLE_H1"
    REDIRECT_PRESENT = "REDIRECT_PRESENT"
    SLOW_RESPONSE = "SLOW_RESPONSE"
    NO_WEBSITE = "NO_WEBSITE"
    FETCH_ERROR = "FETCH_ERROR"
    HAS_DIRECT_CONTACT_PATH = "HAS_DIRECT_CONTACT_PATH"
    HAS_CONVERSION_CTA = "HAS_CONVERSION_CTA"
    HAS_RESERVATION_PATH = "HAS_RESERVATION_PATH"
    HAS_QUOTE_PATH = "HAS_QUOTE_PATH"
    HAS_PRODUCT_DISCOVERY_PATH = "HAS_PRODUCT_DISCOVERY_PATH"
    HAS_SERVICE_DISCOVERY_PATH = "HAS_SERVICE_DISCOVERY_PATH"


class Finding(Entity):
    kind: FindingType
    statement: Text
    value: JsonValue = None
    confidence: Annotated[float, Field(ge=0, le=1)] = 1


class WebsiteAudit(Entity):
    company_id: UUID
    source_id: UUID
    website_url: Text | None = None
    final_url: Text | None = None
    started_at: AwareDatetime = Field(default_factory=utc_now)
    finished_at: AwareDatetime | None = None
    status: AuditStatus = AuditStatus.PENDING
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    response_time_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    body_size_bytes: Annotated[int, Field(ge=0)] = 0
    error_code: Text | None = None
    error_message: Text | None = None
    warnings: tuple[str, ...] = ()
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == AuditStatus.PENDING) != (self.finished_at is None):
            raise ValueError("Final audits require a finish timestamp")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("Finish cannot precede start")
        if self.status == AuditStatus.NO_WEBSITE and self.website_url is not None:
            raise ValueError("NO_WEBSITE must have no website URL")
        return self
