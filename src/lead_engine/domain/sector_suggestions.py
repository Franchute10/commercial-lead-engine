"""Persistent human-reviewed sector suggestion records."""

from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, ConfigDict, model_validator

from lead_engine.domain.models import Text, Timestamped, utc_now
from lead_engine.domain.sectors import SectorSuggestion, SectorSuggestionStatus


class SectorSuggestionRecord(Timestamped):
    """Persisted intake and human disposition; never mutates the official catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    requested_name: Text
    suggested_sector_id: str | None = None
    suggested_subsector_id: str | None = None
    suggested_profile_id: str | None = None
    status: SectorSuggestionStatus = SectorSuggestionStatus.PROVISIONAL
    review_note: Text | None = None
    reviewed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_suggestion(self) -> Self:
        SectorSuggestion(
            requested_name=self.requested_name,
            suggested_sector_id=self.suggested_sector_id,
            suggested_subsector_id=self.suggested_subsector_id,
            suggested_profile_id=self.suggested_profile_id,
            status=self.status,
        )
        if self.status in {SectorSuggestionStatus.ACCEPTED, SectorSuggestionStatus.REJECTED}:
            if self.reviewed_at is None:
                raise ValueError("Terminal suggestion decisions require reviewed_at")
        elif self.reviewed_at is not None:
            raise ValueError("reviewed_at is only set for terminal decisions")
        return self


def revise_suggestion(
    record: SectorSuggestionRecord,
    *,
    status: SectorSuggestionStatus,
    suggested_sector_id: str | None,
    suggested_subsector_id: str | None,
    suggested_profile_id: str | None,
    review_note: str | None,
    terminal: bool,
    now: datetime | None = None,
) -> SectorSuggestionRecord:
    """Validate a new immutable revision of a suggestion."""

    timestamp = now or utc_now()
    return SectorSuggestionRecord.model_validate(
        {
            **record.model_dump(),
            "status": status,
            "suggested_sector_id": suggested_sector_id,
            "suggested_subsector_id": suggested_subsector_id,
            "suggested_profile_id": suggested_profile_id,
            "review_note": review_note,
            "updated_at": timestamp,
            "reviewed_at": timestamp if terminal else None,
        }
    )


__all__ = ["SectorSuggestionRecord", "SectorSuggestionStatus", "revise_suggestion"]
