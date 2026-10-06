"""Immutable drafts, claim provenance and append-only human decisions."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lead_engine.domain.enums import RoleCategory
from lead_engine.domain.models import Entity, Text, utc_now
from lead_engine.domain.research import EvidenceCitation


class OutreachChannel(StrEnum):
    LINKEDIN = "LINKEDIN"
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    PHONE_SCRIPT = "PHONE_SCRIPT"
    WEBSITE_CONTACT_FORM = "WEBSITE_CONTACT_FORM"


class OutreachPurpose(StrEnum):
    FIRST_CONTACT = "FIRST_CONTACT"
    LINKEDIN_CONNECTION = "LINKEDIN_CONNECTION"
    LINKEDIN_MESSAGE = "LINKEDIN_MESSAGE"
    EMAIL_INTRO = "EMAIL_INTRO"
    WHATSAPP_INTRO = "WHATSAPP_INTRO"
    FOLLOW_UP = "FOLLOW_UP"
    POST_CONNECTION_MESSAGE = "POST_CONNECTION_MESSAGE"
    REFERRAL_INTRO = "REFERRAL_INTRO"
    MEETING_REQUEST = "MEETING_REQUEST"


class OutreachLanguage(StrEnum):
    ES = "es"
    EN = "en"


class DraftStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    USED = "USED"


class DraftCreator(StrEnum):
    SYSTEM = "SYSTEM"
    MANUAL = "MANUAL"


class OutreachSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    connection_limit: int = Field(default=300, ge=100, le=300)
    message_limit: int = Field(default=700, ge=200, le=700)
    whatsapp_limit: int = Field(default=600, ge=200, le=1000)
    email_limit: int = Field(default=1200, ge=300, le=2000)
    subject_limit: int = Field(default=80, ge=30, le=100)
    form_limit: int = Field(default=700, ge=200, le=1000)
    phone_limit: int = Field(default=900, ge=200, le=1500)
    duplicate_days: int = Field(default=7, ge=1, le=365)
    referral_freshness_days: int = Field(default=30, ge=1, le=365)

    def body_limit(self, channel: OutreachChannel, connection: bool) -> int:
        if channel == OutreachChannel.LINKEDIN:
            return self.connection_limit if connection else self.message_limit
        return {
            OutreachChannel.EMAIL: self.email_limit,
            OutreachChannel.WHATSAPP: self.whatsapp_limit,
            OutreachChannel.WEBSITE_CONTACT_FORM: self.form_limit,
            OutreachChannel.PHONE_SCRIPT: self.phone_limit,
        }[channel]


class GroundedClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    text: Text
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)


class ReferralObservation(BaseModel):
    """Explicit human attestation, never inferred from a name or a relationship."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
    contact_id: UUID
    referrer_name: Annotated[str, Field(min_length=1, max_length=80)]
    recommended_contact: Annotated[bool, Field(strict=True)]


class OutreachDraft(Entity):
    lead_id: UUID
    contact_id: UUID
    company_name: Text
    contact_name: Text
    contact_role: Text
    role_category: RoleCategory
    created_at: AwareDatetime = Field(default_factory=utc_now)
    outreach_version: Text = "outreach-v1"
    channel: OutreachChannel
    channel_value: Text
    purpose: OutreachPurpose
    language: OutreachLanguage
    subject: Text | None = None
    body: Annotated[str, Field(min_length=1, max_length=2000)]
    connection: bool = False
    supporting_evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    evidence_citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    claims: tuple[GroundedClaim, ...]
    commercial_brief_id: UUID
    shortlist_action: Text
    previous_interaction_id: UUID | None = None
    referral_evidence_id: UUID | None = None
    warnings: tuple[Text, ...] = ()
    created_by: DraftCreator = DraftCreator.SYSTEM
    settings: OutreachSettings = Field(default_factory=OutreachSettings)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        refs = set(self.supporting_evidence_ids)
        if len(refs) != len(self.supporting_evidence_ids):
            raise ValueError("Duplicate evidence references")
        if {c.evidence_id for c in self.evidence_citations} != refs:
            raise ValueError("All evidence requires citation provenance")
        if any(not set(c.evidence_ids) <= refs or c.text not in self.body for c in self.claims):
            raise ValueError("Claim must appear verbatim and cite stored evidence")
        if self.referral_evidence_id and self.referral_evidence_id not in refs:
            raise ValueError("Referral requires evidence")
        if self.purpose == OutreachPurpose.REFERRAL_INTRO and not self.referral_evidence_id:
            raise ValueError("Referral introduction requires explicit evidence")
        if self.connection and self.channel != OutreachChannel.LINKEDIN:
            raise ValueError("Connection applies only to LinkedIn")
        if len(self.body) > self.settings.body_limit(self.channel, self.connection):
            raise ValueError(
                "Draft exceeds channel length; shorten context without truncating facts"
            )
        if self.channel == OutreachChannel.EMAIL and not self.subject:
            raise ValueError("Email requires a subject")
        if self.subject and len(self.subject) > self.settings.subject_limit:
            raise ValueError("Subject exceeds limit")
        return self


class OutreachEvent(Entity):
    draft_id: UUID
    sequence: int = Field(default=1, ge=1)
    occurred_at: AwareDatetime = Field(default_factory=utc_now)
    status: DraftStatus
    reason: Text | None = None
    interaction_id: UUID | None = None
    actor: Text = "HUMAN"


class DraftView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    draft: OutreachDraft
    status: DraftStatus = DraftStatus.DRAFT
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    used_at: datetime | None = None
    events: tuple[OutreachEvent, ...] = ()


class OutreachPolicy(Protocol):
    version: str

    def render(
        self,
        *,
        campaign: str,
        opportunity: str,
        role: RoleCategory,
        language: OutreachLanguage,
        channel: OutreachChannel,
        purpose: OutreachPurpose,
        connection: bool,
        company: str,
        name: str,
        observation: str,
        referral: str,
        authority: bool,
    ) -> tuple[str | None, str]: ...
