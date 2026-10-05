"""Contracts implemented by infrastructure, never by provider-specific business logic."""

from typing import Protocol

from lead_engine.domain.models import Company


class CompanyRepository(Protocol):
    """Future persistence must reject duplicate canonical identities atomically."""

    def get_by_identity(self, identity_key: str) -> Company | None: ...

    def add(self, company: Company) -> None: ...


class DatabaseProbe(Protocol):
    """Read-only connectivity probe."""

    def ping(self) -> None: ...
