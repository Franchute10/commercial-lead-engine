"""Transactional evidence correction, with source observations left intact."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from lead_engine.application.discovery import UnitOfWorkFactory
from lead_engine.application.ports import Repository
from lead_engine.domain.errors import NotFoundError
from lead_engine.domain.evidence_lifecycle import (
    EvidenceDisposition,
    EvidenceDispositionKind,
    active_evidence,
)
from lead_engine.domain.models import Evidence, utc_now


def current_evidence(repo: Repository, company_id: UUID, as_of: datetime) -> tuple[Evidence, ...]:
    return active_evidence(
        repo.list(Evidence, company_id=company_id),
        repo.list(EvidenceDisposition, company_id=company_id),
        as_of,
    )


class EvidenceLifecycleService:
    def __init__(self, factory: UnitOfWorkFactory, clock: Callable[[], datetime] = utc_now) -> None:
        self.factory = factory
        self.clock = clock

    def dispose(
        self,
        evidence_id: UUID,
        kind: EvidenceDispositionKind,
        actor: str,
        reason: str,
        replacement_evidence_id: UUID | None = None,
    ) -> EvidenceDisposition:
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("Evidence lifecycle clock requires an aware timestamp")
        with self.factory() as uow:
            repo = uow.repository
            original = repo.get(Evidence, evidence_id)
            if original is None:
                raise NotFoundError("Evidence not found")
            if original.created_at > now:
                raise ValueError("Cannot dispose future evidence")
            if repo.list(EvidenceDisposition, evidence_id=evidence_id):
                raise ValueError("Evidence is already retracted or superseded")
            event = EvidenceDisposition(
                evidence_id=evidence_id,
                company_id=original.company_id,
                kind=kind,
                replacement_evidence_id=replacement_evidence_id,
                actor=actor,
                reason=reason,
                recorded_at=now,
            )
            if replacement_evidence_id is not None:
                replacement = repo.get(Evidence, replacement_evidence_id)
                if replacement is None:
                    raise NotFoundError("Replacement evidence not found")
                if replacement.company_id != original.company_id:
                    raise ValueError("Replacement must belong to the same company")
                if replacement.created_at > now:
                    raise ValueError("Replacement must already exist")
                if replacement_evidence_id not in {
                    item.id for item in current_evidence(repo, original.company_id, now)
                }:
                    raise ValueError("Replacement evidence is not active")
            repo.add(event)
            uow.commit()
            return event

    def history(self, evidence_id: UUID) -> tuple[Evidence, list[EvidenceDisposition]]:
        with self.factory() as uow:
            evidence = uow.repository.get(Evidence, evidence_id)
            if evidence is None:
                raise NotFoundError("Evidence not found")
            return evidence, uow.repository.list(EvidenceDisposition, evidence_id=evidence_id)
