"""Explicit deterministic identity matching and conflict detection."""

from hashlib import sha256

from lead_engine.application.ports import Repository
from lead_engine.domain.errors import IdentityConflictError
from lead_engine.domain.identity import normalize_domain, normalize_name
from lead_engine.domain.models import Company


def identity_keys(company: Company) -> tuple[str, ...]:
    keys: list[str] = []
    domain = company.primary_domain or (
        normalize_domain(company.website) if company.website else None
    )
    if domain:
        keys.append(f"domain:{domain}")
    if company.city and company.country:
        location = f"{normalize_name(company.city)}|{normalize_name(company.country)}"
        if company.legal_name:
            keys.append(
                "legal:"
                + sha256(f"{normalize_name(company.legal_name)}|{location}".encode()).hexdigest()
            )
        keys.append(
            "name:"
            + sha256(f"{normalize_name(company.canonical_name)}|{location}".encode()).hexdigest()
        )
    return tuple(keys)


def find_duplicate(company: Company, repository: Repository) -> Company | None:
    """Prefer domain, then legal/locality, then name/locality; never silently resolve conflicts."""
    matches: dict[str, Company] = {}
    for key in identity_keys(company):
        match = repository.find_company_identity(key)
        if match:
            matches[key] = match
    if len({match.id for match in matches.values()}) > 1:
        raise IdentityConflictError(
            "Identity signals reference different companies; review required"
        )
    match = next(iter(matches.values()), None)
    if match:
        incoming_domain = company.primary_domain or (
            normalize_domain(company.website) if company.website else None
        )
        stored_domain = match.primary_domain or (
            normalize_domain(match.website) if match.website else None
        )
        if incoming_domain and stored_domain and incoming_domain != stored_domain:
            raise IdentityConflictError("Matching names have conflicting domains; review required")
    return match
