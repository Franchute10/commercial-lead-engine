"""Typed generic persistence with explicit mapping and relational score evidence."""

from datetime import datetime
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
from lead_engine.domain.outreach import OutreachDraft, OutreachEvent
from lead_engine.domain.research import CommercialBrief
from lead_engine.domain.shortlist import DailyShortlistRun, ShortlistSuppression
from lead_engine.infrastructure.orm import (
    CampaignRow,
    CommercialBriefRow,
    CompanyIdentityRow,
    CompanyRow,
    ComponentEvidenceRow,
    ContactIdentityRow,
    ContactRow,
    DailyShortlistRunRow,
    DecisionMakerResearchRunRow,
    DiscoveryRunRow,
    EntityRow,
    EvidenceRow,
    LeadInteractionRow,
    LeadRow,
    LeadScoreRow,
    OutreachDraftRow,
    OutreachEventRow,
    ScoreComponentRow,
    ShortlistSuppressionRow,
    SourceRow,
    WebsiteAuditRow,
)

MAPPINGS: dict[type[Entity], type[EntityRow]] = {
    OutreachDraft: OutreachDraftRow,
    OutreachEvent: OutreachEventRow,
    DailyShortlistRun: DailyShortlistRunRow,
    ShortlistSuppression: ShortlistSuppressionRow,
    CommercialBrief: CommercialBriefRow,
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
            if entity_type is OutreachDraft and field not in {
                "id",
                "lead_id",
                "contact_id",
                "commercial_brief_id",
                "created_at",
            }:
                raise ValueError(f"Unsupported draft filter {field}")
            if entity_type is CommercialBrief and field not in {
                "id",
                "lead_id",
                "company_id",
                "campaign_id",
                "generated_at",
                "research_version",
                "lead_score_id",
            }:
                raise ValueError(f"Unsupported brief filter {field}")
            if entity_type is DailyShortlistRun and field not in {
                "id",
                "generated_at",
                "policy_version",
            }:
                raise ValueError(f"Unsupported shortlist run filter {field}")
            query = query.where(getattr(row_type, field) == value)
        query = query.order_by(
            OutreachEventRow.sequence if entity_type is OutreachEvent else row_type.id
        )
        return [self._to_entity(entity_type, row) for row in self.session.scalars(query)]

    def _to_entity[T: Entity](self, entity_type: type[T], row: EntityRow) -> T:
        if isinstance(row, (CommercialBriefRow, DailyShortlistRunRow, OutreachDraftRow)):
            return entity_type.model_validate(row.payload)
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
        if isinstance(entity, OutreachDraft):
            self.session.add(
                OutreachDraftRow(
                    id=entity.id,
                    lead_id=entity.lead_id,
                    contact_id=entity.contact_id,
                    commercial_brief_id=entity.commercial_brief_id,
                    created_at=entity.created_at,
                    payload=entity.model_dump(mode="json"),
                )
            )
            self.session.flush()
            return
        if isinstance(entity, OutreachEvent):
            # Sequence comes from the application snapshot: a stale concurrent decision
            # must collide, never acquire a new sequence after validating an old status.
            self.session.add(OutreachEventRow(**entity.model_dump()))
            self.session.flush()
            return
        if isinstance(entity, DailyShortlistRun):
            self.session.add(
                DailyShortlistRunRow(
                    id=entity.id,
                    generated_at=entity.generated_at,
                    policy_version=entity.policy_version,
                    payload=entity.model_dump(mode="json"),
                )
            )
            self.session.flush()
            return
        if isinstance(entity, CommercialBrief):
            self.session.add(
                CommercialBriefRow(
                    id=entity.id,
                    lead_id=entity.lead_id,
                    company_id=entity.company_id,
                    campaign_id=entity.campaign_id,
                    generated_at=entity.generated_at,
                    research_version=entity.research_version,
                    lead_score_id=entity.lead_score_id,
                    payload=entity.model_dump(mode="json"),
                )
            )
            self.session.flush()
            return
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

    def revoke_shortlist_suppression(self, suppression_id: UUID, revoked_at: datetime) -> None:
        row = self.session.get(ShortlistSuppressionRow, suppression_id)
        if row is None:
            raise ValueError("Suppression not found")
        record = self._to_entity(ShortlistSuppression, row)
        validated = ShortlistSuppression.model_validate(
            {**record.model_dump(), "revoked_at": revoked_at}
        )
        row.revoked_at = validated.revoked_at
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
