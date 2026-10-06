"""Versioned score policy contracts and persisted presentation metadata."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from lead_engine.domain.audit import WebsiteAudit
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.models import Campaign, Company, Contact, Evidence, Lead, LeadScore


class SignalState(StrEnum):
    UNKNOWN = "UNKNOWN"
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNCERTAIN = "UNCERTAIN"


class ScoreBand(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


@dataclass(frozen=True)
class ScoringContext:
    lead: Lead
    company: Company
    campaign: Campaign
    evidence: tuple[Evidence, ...]
    contacts: tuple[Contact, ...]
    audits: tuple[WebsiteAudit, ...]
    as_of: datetime


class ScoreSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    band: ScoreBand
    completeness_percent: int | None = Field(ge=0, le=100)
    known_weight: int | None = Field(ge=0, le=100)
    bands: tuple[int, int, int, int] = (85, 70, 55, 40)
    audit_id: str | None = None
    input_cutoff: str


@dataclass(frozen=True)
class ScoringResult:
    score: LeadScore
    summary: ScoreSummary
    company_name: str
    campaign_name: str


class CampaignScoringPolicy(Protocol):
    @property
    def version(self) -> str: ...
    @property
    def campaign_type(self) -> CampaignType: ...
    def calculate(self, context: ScoringContext) -> tuple[LeadScore, ScoreSummary]: ...


def score_band(total: Decimal, thresholds: tuple[int, int, int, int]) -> ScoreBand:
    if not (100 >= thresholds[0] > thresholds[1] > thresholds[2] > thresholds[3] > 0):
        raise ValueError("Band thresholds must be descending within 1–100")
    for threshold, band in zip(
        thresholds, (ScoreBand.A, ScoreBand.B, ScoreBand.C, ScoreBand.D), strict=True
    ):
        if total >= threshold:
            return band
    return ScoreBand.E
