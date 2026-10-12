"""Append-only dispositions of evidence; original observations never change."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from lead_engine.domain.models import Entity, Evidence, Text, utc_now


class EvidenceDispositionKind(StrEnum):
    RETRACTED = "RETRACTED"
    SUPERSEDED = "SUPERSEDED"


class EvidenceDisposition(Entity):
    evidence_id: UUID
    company_id: UUID
    kind: EvidenceDispositionKind
    replacement_evidence_id: UUID | None = None
    actor: Text
    reason: Text
    recorded_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def valid_replacement(self) -> "EvidenceDisposition":
        if (self.kind == EvidenceDispositionKind.SUPERSEDED) != (
            self.replacement_evidence_id is not None
        ):
            raise ValueError("Only supersession requires a replacement evidence ID")
        if self.replacement_evidence_id == self.evidence_id:
            raise ValueError("Evidence cannot supersede itself")
        return self


def active_evidence(
    evidence: list[Evidence], dispositions: list[EvidenceDisposition], as_of: datetime
) -> tuple[Evidence, ...]:
    """Select active observations at a cutoff; archives remain separately queryable."""
    inactive = {event.evidence_id for event in dispositions if event.recorded_at <= as_of}
    return tuple(
        item
        for item in evidence
        if item.id not in inactive and item.created_at <= as_of and item.observed_at <= as_of
    )
