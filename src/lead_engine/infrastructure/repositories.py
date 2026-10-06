"""Typed generic persistence with explicit mapping and relational score evidence."""

from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_engine.domain.audit import WebsiteAudit
from lead_engine.domain.contact_research import ContactIdentity, DecisionMakerResearchRun
from lead_engine.domain.discovery import DiscoveryRun
from lead_engine.domain.errors import DuplicateError, IdentityConflictError
from lead_engine.domain.models import (
    Campaign,
    Company,
    Contact,
    Entity,
    Evidence,
    Lead,
    LeadInteraction,
    LeadScore,
    ScoreComponent,
    Source,
)
from lead_engine.infrastructure.orm import (
    CampaignRow,
    CompanyIdentityRow,
    CompanyRow,
    ComponentEvidenceRow,
    ContactIdentityRow,
    ContactRow,
    DecisionMakerResearchRunRow,
    DiscoveryRunRow,
    EntityRow,
    EvidenceRow,
    LeadInteractionRow,
    LeadRow,
    LeadScoreRow,
    ScoreComponentRow,
    SourceRow,
    WebsiteAuditRow,
)

MAPPINGS: dict[type[Entity], type[EntityRow]] = {
    ContactIdentity: ContactIdentityRow,
    DecisionMakerResearchRun: DecisionMakerResearchRunRow,
    Company: CompanyRow,
    WebsiteAudit: WebsiteAuditRow,
    DiscoveryRun: DiscoveryRunRow,
    Contact: ContactRow,
    Source: SourceRow,
    Evidence: EvidenceRow,
    Campaign: CampaignRow,
    Lead: LeadRow,
    LeadScore: LeadScoreRow,
    ScoreComponent: ScoreComponentRow,
    LeadInteraction: LeadInteractionRow,
}


class SqlAlchemyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get[T: Entity](self, entity_type: type[T], entity_id: UUID) -> T | None:
        row = self.session.get(MAPPINGS[entity_type], entity_id)
        return self._to_entity(entity_type, row) if row else None

    def list[T: Entity](self, entity_type: type[T], **filters: object) -> list[T]:
        row_type = MAPPINGS[entity_type]
        query = select(row_type)
        for field, value in filters.items():
            if field not in entity_type.model_fields or field in {"components", "evidence_ids"}:
                raise ValueError(f"Unsupported filter {field}")
            query = query.where(getattr(row_type, field) == value)
        query = query.order_by(row_type.id)
        return [self._to_entity(entity_type, row) for row in self.session.scalars(query)]

    def _to_entity[T: Entity](self, entity_type: type[T], row: EntityRow) -> T:
        data = {
            field: getattr(row, "source_metadata" if field == "metadata" else field)
            for field in entity_type.model_fields
            if field not in {"components", "evidence_ids"}
        }
        if entity_type is LeadScore:
            data["components"] = tuple(self.list(ScoreComponent, lead_score_id=row.id))
        if entity_type is ScoreComponent:
            data["evidence_ids"] = tuple(
                self.session.scalars(
                    select(ComponentEvidenceRow.evidence_id)
                    .where(ComponentEvidenceRow.component_id == row.id)
                    .order_by(ComponentEvidenceRow.evidence_id)
                )
            )
        return entity_type.model_validate(data)

    def add(self, entity: Entity) -> None:
        data = entity.model_dump(exclude={"components", "evidence_ids"})
        if isinstance(entity, Source):
            data["source_metadata"] = data.pop("metadata")
        if isinstance(entity, DiscoveryRun):
            data["outcomes"] = entity.model_dump(mode="json")["outcomes"]
        row = MAPPINGS[type(entity)](**data)
        self.session.add(row)
        self.session.flush()
        if isinstance(entity, LeadScore):
            for component in entity.components:
                self.add(component)
        if isinstance(entity, ScoreComponent):
            for evidence_id in entity.evidence_ids:
                self.session.add(
                    ComponentEvidenceRow(component_id=entity.id, evidence_id=evidence_id)
                )
            self.session.flush()

    def update_company(self, company: Company) -> None:
        row = self.session.get(CompanyRow, company.id)
        if row is None:
            raise ValueError("Cannot update missing company")
        for field, value in company.model_dump().items():
            setattr(row, field, value)
        self.session.flush()

    def update_discovery_run(self, run: DiscoveryRun) -> None:
        run = DiscoveryRun.model_validate(run.model_dump())
        row = self.session.get(DiscoveryRunRow, run.id)
        if row is None:
            raise ValueError("Cannot update missing discovery run")
        data = run.model_dump()
        data["outcomes"] = run.model_dump(mode="json")["outcomes"]
        for field, value in data.items():
            setattr(row, field, value)
        self.session.flush()

    def find_company_identity(self, key: str) -> Company | None:
        row = self.session.get(CompanyIdentityRow, key)
        return self.get(Company, row.company_id) if row else None

    def add_company_identity(self, key: str, company_id: UUID) -> None:
        row = self.session.get(CompanyIdentityRow, key)
        if row:
            if row.company_id != company_id:
                raise IdentityConflictError("Identity already belongs to another company")
            return
        self.session.add(CompanyIdentityRow(key=key, company_id=company_id))
        self.session.flush()


class SqlAlchemyUnitOfWork:
    """Rollback on exit unless committed. One explicit transaction per context."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session: Session | None = None
        self._repository: SqlAlchemyRepository | None = None

    @property
    def repository(self) -> SqlAlchemyRepository:
        if self._repository is None:
            raise RuntimeError("Enter unit of work before using repository")
        return self._repository

    def __enter__(self) -> Self:
        self._session = Session(self._engine)
        self._repository = SqlAlchemyRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("No active transaction")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.rollback()
        if self._session is not None:
            self._session.close()
        self._session = None
        self._repository = None
        if isinstance(exc, IntegrityError):
            # Database constraints protect concurrent writes too. Caller retries in a fresh UoW.
            raise DuplicateError(
                "Database constraint rejected write; review identity/relationships"
            ) from exc
