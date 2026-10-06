"""Structured manual commercial signals; observation vocabulary is separate from policy."""

import json
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from lead_engine.domain.models import Evidence


class BusinessSignal(StrEnum):
    GOOGLE_REVIEW_COUNT = "GOOGLE_REVIEW_COUNT"
    GOOGLE_RATING = "GOOGLE_RATING"
    INSTAGRAM_ACTIVE = "INSTAGRAM_ACTIVE"
    FACEBOOK_ACTIVE = "FACEBOOK_ACTIVE"
    MULTIPLE_LOCATIONS = "MULTIPLE_LOCATIONS"
    HIGH_VALUE_SERVICE = "HIGH_VALUE_SERVICE"
    HIGH_VALUE_PRODUCT = "HIGH_VALUE_PRODUCT"
    PRIVATE_EVENTS = "PRIVATE_EVENTS"
    ECOMMERCE = "ECOMMERCE"
    DISTRIBUTION_NETWORK = "DISTRIBUTION_NETWORK"
    B2B_OPERATION = "B2B_OPERATION"
    PAID_ADVERTISING_ACTIVE = "PAID_ADVERTISING_ACTIVE"
    RECENT_EXPANSION = "RECENT_EXPANSION"
    NEW_LOCATION = "NEW_LOCATION"
    ACTIVE_HIRING = "ACTIVE_HIRING"
    RECENT_REBRAND = "RECENT_REBRAND"
    CORPORATE_CLIENTS = "CORPORATE_CLIENTS"
    EXPORT_ACTIVITY = "EXPORT_ACTIVITY"
    DECISION_MAKER_ACCESS = "DECISION_MAKER_ACCESS"
    COMMERCIAL_NOTE = "COMMERCIAL_NOTE"


class DecisionMakerObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contact_id: UUID
    authority_confirmed: bool
    reachable: bool


class CommercialEvidence(Evidence):
    @model_validator(mode="after")
    def structured_value(self) -> Self:
        kind = BusinessSignal(self.evidence_type)
        value = self.raw_value
        if kind == BusinessSignal.GOOGLE_REVIEW_COUNT:
            if type(value) is not int or value < 0:
                raise ValueError("Review count must be a nonnegative integer")
        elif kind == BusinessSignal.GOOGLE_RATING:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError("Rating must be a decimal between 0 and 5")
            try:
                rating = Decimal(str(value))
            except InvalidOperation as error:
                raise ValueError("Invalid rating") from error
            if not rating.is_finite() or not 0 <= rating <= 5:
                raise ValueError("Rating must be between 0 and 5")
        elif kind == BusinessSignal.DECISION_MAKER_ACCESS:
            if not isinstance(value, dict):
                raise ValueError("Decision-maker access requires a structured JSON observation")
            # JSON IDs are strings, other observation fields remain strict booleans.
            DecisionMakerObservation.model_validate_json(json.dumps(value))
        elif kind == BusinessSignal.COMMERCIAL_NOTE:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Commercial note must be nonempty text")
        elif type(value) is not bool:
            raise ValueError("This commercial signal requires an explicit boolean")
        return self
