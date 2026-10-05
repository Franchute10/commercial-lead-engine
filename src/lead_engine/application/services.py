"""Validated local write operations. Caller owns the unit-of-work transaction."""

from uuid import UUID

from lead_engine.application.identity import find_duplicate, identity_keys
from lead_engine.application.ports import UnitOfWork
from lead_engine.domain.enums import LeadStatus
from lead_engine.domain.errors import (
    DuplicateError,
    IdentityConflictError,
    NotFoundError,
    RelationshipError,
)
from lead_engine.domain.models import (
    Campaign,
    Company,
    Contact,
    Entity,
    Evidence,
    Lead,
    LeadInteraction,
    LeadScore,
    Source,
    utc_now,
)


class LeadService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.repository = uow.repository

    def _require[T: Entity](self, model: type[T], entity_id: UUID) -> T:
        value = self.repository.get(model, entity_id)
        if value is None:
            raise NotFoundError(f"{model.__name__} {entity_id} does not exist")
        return value

    def upsert_company(self, company: Company) -> Company:
        # Revalidate even if callers bypassed Pydantic validation with model_copy/construct.
        company = Company.model_validate(company.model_dump())
        existing = find_duplicate(company, self.repository)
        by_id = self.repository.get(Company, company.id)
        if by_id and existing and by_id.id != existing.id:
            raise IdentityConflictError("Company ID and identity signals disagree")
        existing = existing or by_id
        if existing:
            existing_domains = [key for key in identity_keys(existing) if key.startswith("domain:")]
            incoming_domains = [key for key in identity_keys(company) if key.startswith("domain:")]
            if existing_domains and incoming_domains and existing_domains != incoming_domains:
                raise IdentityConflictError("Company ID has conflicting domain; review required")
            # Preserve known values; enrich missing fields rather than overwrite conflicting facts.
            data = existing.model_dump()
            for field, value in company.model_dump().items():
                if field not in {"id", "created_at", "updated_at"} and data[field] is None:
                    data[field] = value
            data["updated_at"] = utc_now()
            company = Company.model_validate(data)
            self.repository.update_company(company)
        else:
            self.repository.add(company)
        for key in identity_keys(company):
            self.repository.add_company_identity(key, company.id)
        return company

    def create_contact(self, contact: Contact) -> Contact:
        contact = Contact.model_validate(contact.model_dump())
        self._require(Company, contact.company_id)
        self.repository.add(contact)
        return contact

    def add_source(self, source: Source) -> Source:
        source = Source.model_validate(source.model_dump())
        self.repository.add(source)
        return source

    def add_evidence(self, evidence: Evidence) -> Evidence:
        evidence = Evidence.model_validate(evidence.model_dump())
        self._require(Company, evidence.company_id)
        self._require(Source, evidence.source_id)
        self.repository.add(evidence)
        return evidence

    def create_campaign(self, campaign: Campaign) -> Campaign:
        campaign = Campaign.model_validate(campaign.model_dump())
        self.repository.add(campaign)
        return campaign

    def create_lead(self, lead: Lead) -> Lead:
        lead = Lead.model_validate(lead.model_dump())
        self._require(Company, lead.company_id)
        self._require(Campaign, lead.campaign_id)
        if self.repository.list(Lead, company_id=lead.company_id, campaign_id=lead.campaign_id):
            raise DuplicateError("Company already participates in this campaign")
        self.repository.add(lead)
        return lead

    def record_lead_score(self, score: LeadScore) -> LeadScore:
        score = LeadScore.model_validate(score.model_dump())
        lead = self._require(Lead, score.lead_id)
        for component in score.components:
            for evidence_id in component.evidence_ids:
                evidence = self._require(Evidence, evidence_id)
                if evidence.company_id != lead.company_id:
                    raise RelationshipError("Score evidence belongs to another company")
        self.repository.add(score)
        return score

    def record_interaction(self, interaction: LeadInteraction) -> LeadInteraction:
        interaction = LeadInteraction.model_validate(interaction.model_dump())
        lead = self._require(Lead, interaction.lead_id)
        if interaction.contact_id:
            contact = self._require(Contact, interaction.contact_id)
            if contact.company_id != lead.company_id:
                raise RelationshipError("Interaction contact belongs to another company")
        self.repository.add(interaction)
        return interaction

    def retrieve_company(self, company_id: UUID) -> Company:
        return self._require(Company, company_id)

    def list_leads(self, campaign_id: UUID, status: LeadStatus | None = None) -> list[Lead]:
        self._require(Campaign, campaign_id)
        if status is None:
            return self.repository.list(Lead, campaign_id=campaign_id)
        return self.repository.list(Lead, campaign_id=campaign_id, status=status)
