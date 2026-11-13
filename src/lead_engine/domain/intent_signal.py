"""Evidence-backed records for explicit service requests found by a human operator."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from lead_engine.domain.identity import normalize_url
from lead_engine.domain.models import Entity


class SignalPlatform(StrEnum):
    FACEBOOK_GROUP = "FACEBOOK_GROUP"
    REDDIT = "REDDIT"
    NEXTDOOR = "NEXTDOOR"
    PUBLIC_WEB = "PUBLIC_WEB"
    OTHER = "OTHER"


class RequestType(StrEnum):
    SERVICE_REQUEST = "SERVICE_REQUEST"
    QUOTE_REQUEST = "QUOTE_REQUEST"
    RECOMMENDATION_REQUEST = "RECOMMENDATION_REQUEST"


SignalText = Annotated[str, Field(min_length=1, max_length=1000)]
Score = Annotated[int, Field(ge=0, le=100)]


class IntentSignal(Entity):
    """A public post explicitly asking for a service, captured with minimal evidence."""

    campaign_id: UUID
    platform: SignalPlatform
    source_url: Annotated[str, Field(min_length=1, max_length=2048)]
    request_type: RequestType
    service_requested: SignalText
    evidence_excerpt: SignalText
    city: SignalText | None = None
    region: SignalText | None = None
    country: SignalText | None = None
    posted_at: AwareDatetime | None = None
    captured_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    intent_score: Score = 0
    score_explanation: Annotated[str, Field(max_length=2000)] = ""
    qualified_candidate: bool = False

    @field_validator("source_url")
    @classmethod
    def public_http_url(cls, value: str) -> str:
        normalized = normalize_url(value)
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("source_url must be an HTTP(S) URL")
        return normalized

    @model_validator(mode="after")
    def timestamps_are_sensible(self) -> Self:
        if self.posted_at is not None and self.posted_at > self.captured_at:
            raise ValueError("posted_at must not be later than captured_at")
        return self
