"""Persisted discovery audit trail; counts derive from committed candidate outcomes."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from lead_engine.domain.models import Entity, Text, utc_now


class DiscoveryStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class CandidateStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class DiscoveryOutcome(Entity):
    candidate_number: int = Field(ge=1)
    name: str | None = None
    external_id: str | None = None
    row_number: int | None = None
    status: CandidateStatus
    source_id: UUID
    company_id: UUID | None = None
    lead_id: UUID | None = None
    company_created: bool = False
    lead_created: bool = False
    message: str | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status == CandidateStatus.ACCEPTED:
            if self.company_id is None or self.lead_id is None:
                raise ValueError("Accepted candidate requires company and lead")
        elif self.company_created or self.lead_created or self.company_id or self.lead_id:
            raise ValueError("Unaccepted candidate must not claim committed business writes")
        return self


class DiscoveryRun(Entity):
    campaign_id: UUID
    provider: Text
    query: dict[str, JsonValue]
    started_at: AwareDatetime = Field(default_factory=utc_now)
    finished_at: AwareDatetime | None = None
    status: DiscoveryStatus = DiscoveryStatus.RUNNING
    outcomes: tuple[DiscoveryOutcome, ...] = ()
    provider_error: str | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.finished_at and self.finished_at < self.started_at:
            raise ValueError("Discovery finish precedes start")
        if (self.status == DiscoveryStatus.RUNNING) != (self.finished_at is None):
            raise ValueError("Finished runs require finished_at; running runs must not have it")
        if [outcome.candidate_number for outcome in self.outcomes] != list(
            range(1, len(self.outcomes) + 1)
        ):
            raise ValueError("Candidate outcomes must be sequential")
        return self

    @property
    def counts(self) -> dict[str, int]:
        accepted = [item for item in self.outcomes if item.status == CandidateStatus.ACCEPTED]
        return {
            "candidates": len(self.outcomes),
            "companies_created": sum(item.company_created for item in accepted),
            "companies_reused": sum(not item.company_created for item in accepted),
            "conflicts": sum(item.status == CandidateStatus.CONFLICT for item in self.outcomes),
            "rejected": sum(item.status == CandidateStatus.REJECTED for item in self.outcomes),
            "errors": sum(item.status == CandidateStatus.ERROR for item in self.outcomes),
            "leads_created": sum(item.lead_created for item in accepted),
            "leads_reused": sum(not item.lead_created for item in accepted),
        }
