"""Scout orchestrates providers, exact identity and auditable per-candidate transactions."""

from itertools import islice
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError

from lead_engine.application.discovery import (
    DiscoveryCandidate,
    DiscoveryProvider,
    DiscoveryQuery,
    UnitOfWorkFactory,
)
from lead_engine.application.identity import find_duplicate
from lead_engine.application.services import LeadService
from lead_engine.domain.discovery import (
    CandidateStatus,
    DiscoveryOutcome,
    DiscoveryRun,
    DiscoveryStatus,
)
from lead_engine.domain.errors import DuplicateError, IdentityConflictError, NotFoundError
from lead_engine.domain.identity import normalize_domain, normalize_name
from lead_engine.domain.models import Campaign, Company, Evidence, Lead, Source, utc_now


class ScoutService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def discover(self, query: DiscoveryQuery, provider: DiscoveryProvider) -> DiscoveryRun:
        query = DiscoveryQuery.model_validate(query.model_dump())
        run = DiscoveryRun(
            campaign_id=query.campaign_id,
            provider=provider.name,
            query=query.model_dump(mode="json"),
        )
        with self._uow_factory() as uow:
            if uow.repository.get(Campaign, query.campaign_id) is None:
                raise NotFoundError("Discovery requires an existing campaign")
            uow.repository.add(run)
            uow.commit()
        iterator = None
        provider_error = None
        try:
            iterator = iter(provider.discover(query))
            for number, candidate in enumerate(islice(iterator, query.limit), start=1):
                run = self._candidate(run, candidate, number, provider)
        except (ValueError, OSError) as error:
            provider_error = f"Discovery input error: {error}"
        except Exception:
            # Do not expose provider exceptions containing URLs/credentials or database internals.
            provider_error = (
                "Discovery interrupted while reading/persisting candidates; "
                "verify input and database"
            )
        finally:
            close = getattr(iterator, "close", None) if iterator is not None else None
            if close is not None:
                close()
        if provider_error:
            status = DiscoveryStatus.FAILED
        elif any(run.counts[key] for key in ("conflicts", "rejected", "errors")):
            status = DiscoveryStatus.PARTIAL
        else:
            status = DiscoveryStatus.COMPLETED
        run = self._replace(
            run, finished_at=utc_now(), status=status, provider_error=provider_error
        )
        with self._uow_factory() as uow:
            uow.repository.update_discovery_run(run)
            uow.commit()
        return run

    def _replace(self, run: DiscoveryRun, **changes: object) -> DiscoveryRun:
        return DiscoveryRun.model_validate({**run.model_dump(), **changes})

    def _source(
        self,
        candidate: DiscoveryCandidate,
        run: DiscoveryRun,
        provider: DiscoveryProvider,
        number: int,
        *,
        fallback: bool = False,
    ) -> Source:
        # Provider metadata is namespaced so it cannot override authoritative run references.
        values = {
            "source_type": provider.source_type,
            "url": candidate.source_url,
            "title": candidate.source_title,
            "retrieved_at": candidate.retrieved_at,
            "metadata": {
                "discovery_run_id": str(run.id),
                "provider": provider.name,
                "candidate_number": number,
                "provider_metadata": candidate.metadata,
                "candidate": candidate.model_dump(mode="json"),
            },
        }
        try:
            return Source.model_validate(values)
        except ValueError:
            if not fallback:
                raise
            # Preserve raw invalid provenance in metadata while keeping the Source contract valid.
            return Source.model_validate({**values, "url": None, "title": None})

    def _outcome(
        self,
        candidate: DiscoveryCandidate,
        number: int,
        source_id: UUID,
        status: CandidateStatus,
        **details: object,
    ) -> DiscoveryOutcome:
        row = candidate.metadata.get("row_number")
        return DiscoveryOutcome.model_validate(
            {
                "candidate_number": number,
                "name": candidate.name,
                "external_id": candidate.external_id,
                "row_number": row if isinstance(row, int) else None,
                "source_id": source_id,
                "status": status,
                **details,
            }
        )

    def _candidate(
        self,
        run: DiscoveryRun,
        candidate: DiscoveryCandidate,
        number: int,
        provider: DiscoveryProvider,
    ) -> DiscoveryRun:
        try:
            # All business records, their evidence, and outcome commit atomically.
            with self._uow_factory() as uow:
                company = candidate.to_company()
                source = self._source(candidate, run, provider, number)
                existing = find_duplicate(company, uow.repository)
                if existing:
                    conflicts = conflicting_fields(existing, company)
                    if conflicts:
                        raise IdentityConflictError(
                            "Conflicting known fields: " + ", ".join(conflicts)
                        )
                service = LeadService(uow)
                company = service.upsert_company(company)
                service.add_source(source)
                service.add_evidence(
                    Evidence(
                        company_id=company.id,
                        source_id=source.id,
                        evidence_type="DISCOVERY",
                        statement=(
                            "Company candidate was supplied by this source; facts are unverified"
                        ),
                        raw_value=candidate.model_dump(mode="json"),
                        confidence=1,
                        observed_at=candidate.retrieved_at,
                    )
                )
                leads = uow.repository.list(
                    Lead, company_id=company.id, campaign_id=run.campaign_id
                )
                lead = (
                    leads[0]
                    if leads
                    else service.create_lead(
                        Lead(
                            company_id=company.id,
                            campaign_id=run.campaign_id,
                        )
                    )
                )
                outcome = self._outcome(
                    candidate,
                    number,
                    source.id,
                    CandidateStatus.ACCEPTED,
                    company_id=company.id,
                    lead_id=lead.id,
                    company_created=existing is None,
                    lead_created=not leads,
                )
                updated = self._replace(run, outcomes=(*run.outcomes, outcome))
                uow.repository.update_discovery_run(updated)
                uow.commit()
                return updated
        except (IdentityConflictError, DuplicateError) as error:
            status, message = CandidateStatus.CONFLICT, str(error)
        except ValidationError as error:
            status = CandidateStatus.REJECTED
            message = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in error.errors(include_input=False, include_url=False)
            )
        except ValueError as error:
            status, message = CandidateStatus.REJECTED, str(error)
        except Exception:
            status, message = (
                CandidateStatus.ERROR,
                "Candidate transaction failed; no business writes committed",
            )
        # Business writes rolled back. Audit the untrusted input in a separate transaction.
        with self._uow_factory() as uow:
            source = self._source(candidate, run, provider, number, fallback=True)
            LeadService(uow).add_source(source)
            outcome = self._outcome(candidate, number, source.id, status, message=message)
            updated = self._replace(run, outcomes=(*run.outcomes, outcome))
            uow.repository.update_discovery_run(updated)
            uow.commit()
        return updated


def conflicting_fields(existing: Company, incoming: Company) -> list[str]:
    conflicts = []
    for field in Company.model_fields.keys() - {"id", "created_at", "updated_at"}:
        old, new = getattr(existing, field), getattr(incoming, field)
        if old is not None and new is not None and not _equivalent(field, old, new):
            conflicts.append(field)
    return sorted(conflicts)


def _equivalent(field: str, old: str, new: str) -> bool:
    if field in {
        "canonical_name",
        "legal_name",
        "city",
        "country",
        "region",
        "industry",
        "subindustry",
    }:
        return normalize_name(old) == normalize_name(new)
    if field == "website":
        a, b = urlsplit(old), urlsplit(new)
        return (normalize_domain(old), a.path.rstrip("/"), a.query) == (
            normalize_domain(new),
            b.path.rstrip("/"),
            b.query,
        )
    if field == "email":
        return old.casefold() == new.casefold()
    return old == new
