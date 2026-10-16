"""Human-controlled lifecycle for sector intake suggestions."""

from datetime import UTC, datetime
from uuid import UUID

from lead_engine.application.ports import UnitOfWork
from lead_engine.domain.sector_suggestions import (
    SectorSuggestionRecord,
    SectorSuggestionStatus,
    revise_suggestion,
)
from lead_engine.domain.sectors import create_sector_suggestion


class SectorSuggestionService:
    """Manage suggestion records; callers own and commit the transaction."""

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def submit(
        self,
        requested_name: str,
        suggested_sector_id: str | None = None,
        suggested_subsector_id: str | None = None,
        suggested_profile_id: str | None = None,
    ) -> SectorSuggestionRecord:
        value = create_sector_suggestion(
            requested_name,
            suggested_sector_id,
            suggested_subsector_id,
            suggested_profile_id,
        )
        record = SectorSuggestionRecord(**value.model_dump())
        self.uow.repository.add(record)
        return record

    def list(self) -> list[SectorSuggestionRecord]:
        return sorted(
            self.uow.repository.list(SectorSuggestionRecord),
            key=lambda record: (record.created_at, str(record.id)),
        )

    def get(self, suggestion_id: UUID) -> SectorSuggestionRecord:
        record = self.uow.repository.get(SectorSuggestionRecord, suggestion_id)
        if record is None:
            raise ValueError(f"Sector suggestion not found: {suggestion_id}")
        return record

    def mark_needs_review(
        self,
        suggestion_id: UUID,
        suggested_sector_id: str | None = None,
        suggested_subsector_id: str | None = None,
        suggested_profile_id: str | None = None,
        review_note: str | None = None,
    ) -> SectorSuggestionRecord:
        record = self._pending(suggestion_id)
        if record.status is not SectorSuggestionStatus.PROVISIONAL:
            raise ValueError("Only provisional suggestions can be marked for review")
        updated = self._revise_mapping(
            record,
            SectorSuggestionStatus.NEEDS_REVIEW,
            suggested_sector_id,
            suggested_subsector_id,
            suggested_profile_id,
            review_note,
            terminal=False,
        )
        self.uow.repository.update_sector_suggestion(updated)
        return updated

    def accept(
        self,
        suggestion_id: UUID,
        suggested_sector_id: str | None = None,
        suggested_subsector_id: str | None = None,
        suggested_profile_id: str | None = None,
        review_note: str | None = None,
    ) -> SectorSuggestionRecord:
        record = self._pending(suggestion_id)
        updated = self._revise_mapping(
            record,
            SectorSuggestionStatus.ACCEPTED,
            suggested_sector_id,
            suggested_subsector_id,
            suggested_profile_id,
            review_note,
            terminal=True,
        )
        if updated.suggested_sector_id is None:
            raise ValueError("Accepting a suggestion requires an existing sector mapping")
        self.uow.repository.update_sector_suggestion(updated)
        return updated

    def reject(self, suggestion_id: UUID, review_note: str) -> SectorSuggestionRecord:
        record = self._pending(suggestion_id)
        if not review_note.strip():
            raise ValueError("Rejecting a suggestion requires a nonblank review note")
        updated = revise_suggestion(
            record,
            status=SectorSuggestionStatus.REJECTED,
            suggested_sector_id=record.suggested_sector_id,
            suggested_subsector_id=record.suggested_subsector_id,
            suggested_profile_id=record.suggested_profile_id,
            review_note=review_note,
            terminal=True,
            now=datetime.now(UTC),
        )
        self.uow.repository.update_sector_suggestion(updated)
        return updated

    def _pending(self, suggestion_id: UUID) -> SectorSuggestionRecord:
        record = self.get(suggestion_id)
        if record.status in {SectorSuggestionStatus.ACCEPTED, SectorSuggestionStatus.REJECTED}:
            raise ValueError("Terminal sector suggestions cannot be changed")
        return record

    @staticmethod
    def _revise_mapping(
        record: SectorSuggestionRecord,
        status: SectorSuggestionStatus,
        sector_id: str | None,
        subsector_id: str | None,
        profile_id: str | None,
        review_note: str | None,
        *,
        terminal: bool,
    ) -> SectorSuggestionRecord:
        resolved_sector = sector_id if sector_id is not None else record.suggested_sector_id
        if sector_id is not None and sector_id != record.suggested_sector_id:
            resolved_subsector = subsector_id
        else:
            resolved_subsector = (
                subsector_id if subsector_id is not None else record.suggested_subsector_id
            )
        if profile_id is not None:
            resolved_profile = profile_id
        elif sector_id is not None and sector_id != record.suggested_sector_id:
            resolved_profile = None
        else:
            resolved_profile = record.suggested_profile_id
        resolved_note = review_note if review_note is not None else record.review_note
        return revise_suggestion(
            record,
            status=status,
            suggested_sector_id=resolved_sector,
            suggested_subsector_id=resolved_subsector,
            suggested_profile_id=resolved_profile,
            review_note=resolved_note,
            terminal=terminal,
        )
