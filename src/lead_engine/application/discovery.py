"""Untrusted candidate DTOs and provider-neutral discovery contracts."""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from lead_engine.application.ports import UnitOfWork
from lead_engine.domain.enums import SourceType
from lead_engine.domain.models import Company, utc_now


class DiscoveryQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
    campaign_id: UUID
    country: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    industry: str | None = None
    city: str | None = None
    region: str | None = None
    query_text: str | None = None
    limit: Annotated[int, Field(ge=1, le=10000)] = 1000


class DiscoveryCandidate(BaseModel):
    """Raw fields intentionally tolerate missing/invalid facts until Scout validates them."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    external_id: str | None = None
    name: str | None = None
    legal_name: str | None = None
    website: str | None = None
    primary_domain: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    industry: str | None = None
    subindustry: str | None = None
    instagram_url: str | None = None
    facebook_url: str | None = None
    linkedin_url: str | None = None
    google_maps_url: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    retrieved_at: AwareDatetime = Field(default_factory=utc_now)
    validation_error: str | None = None

    def to_company(self) -> Company:
        if self.validation_error:
            raise ValueError(self.validation_error)
        fields = {
            field: value.strip() or None if isinstance(value, str) else value
            for field, value in self.model_dump().items()
            if field in Company.model_fields
        }
        fields["canonical_name"] = self.name.strip() if self.name else ""
        return Company.model_validate(fields)


class DiscoveryProvider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def source_type(self) -> SourceType: ...
    def discover(self, query: DiscoveryQuery) -> Iterable[DiscoveryCandidate]: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[UnitOfWork]: ...
