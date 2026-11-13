"""Human-in-the-loop capture and deterministic qualification of service requests."""

from datetime import datetime
from uuid import UUID

from lead_engine.application.ports import UnitOfWork
from lead_engine.domain.errors import DuplicateError, NotFoundError
from lead_engine.domain.identity import normalize_name
from lead_engine.domain.intent_signal import IntentSignal, RequestType, SignalPlatform
from lead_engine.domain.models import Campaign, utc_now


class IntentSignalService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.repository = uow.repository

    def capture(
        self,
        *,
        campaign_id: UUID,
        platform: SignalPlatform,
        source_url: str,
        request_type: RequestType,
        service_requested: str,
        evidence_excerpt: str,
        city: str | None = None,
        region: str | None = None,
        country: str | None = None,
        posted_at: datetime | None = None,
        eligible_places: tuple[str, ...] = (),
    ) -> IntentSignal:
        campaign = self.repository.get(Campaign, campaign_id)
        if campaign is None:
            raise NotFoundError(f"Campaign {campaign_id} does not exist")
        candidate = IntentSignal(
            campaign_id=campaign_id,
            platform=platform,
            source_url=source_url,
            request_type=request_type,
            service_requested=service_requested,
            evidence_excerpt=evidence_excerpt,
            city=city,
            region=region,
            country=country,
            posted_at=posted_at,
        )
        if self.repository.list(IntentSignal, source_url=candidate.source_url):
            raise DuplicateError("This public post has already been captured")

        now = utc_now()
        age_days = (now - candidate.posted_at).total_seconds() / 86400 if candidate.posted_at else None
        points = {
            RequestType.SERVICE_REQUEST: 35,
            RequestType.QUOTE_REQUEST: 40,
            RequestType.RECOMMENDATION_REQUEST: 25,
        }[candidate.request_type]
        reasons = [f"Explicit {candidate.request_type.value.lower().replace('_', ' ')}: +{points}"]
        if normalize_name(candidate.service_requested):
            points += 20
            reasons.append("Service requested is identified: +20")

        expected_places = [
            campaign.city,
            campaign.region,
            campaign.country,
            campaign.geography,
            *eligible_places,
        ]
        actual_places = [candidate.city, candidate.region, candidate.country]
        normalized_actual = [normalize_name(value or "") for value in actual_places]
        matched_place = any(
            expected and normalize_name(expected) in normalized_actual
            for expected in expected_places
        )
        if matched_place:
            points += 25
            reasons.append("Location matches the campaign: +25")
        else:
            reasons.append("Campaign location not confirmed: +0")

        fresh = age_days is not None and 0 <= age_days <= 30
        if age_days is not None and 0 <= age_days <= 7:
            points += 20
            reasons.append("Posted within the last 7 days: +20")
        elif age_days is not None and 7 < age_days <= 30:
            points += 10
            reasons.append("Posted within the last 30 days: +10")
        elif age_days is None:
            reasons.append("Post date not provided: +0")
        else:
            reasons.append("Post is older than 30 days: +0")

        record = IntentSignal.model_validate(
            {
                **candidate.model_dump(),
                "captured_at": now,
                "intent_score": min(points, 100),
                "score_explanation": "; ".join(reasons),
                "qualified_candidate": matched_place and fresh and points >= 75,
            }
        )
        self.repository.add(record)
        return record

    def list_for_campaign(self, campaign_id: UUID | None = None) -> list[IntentSignal]:
        if campaign_id is not None and self.repository.get(Campaign, campaign_id) is None:
            raise NotFoundError(f"Campaign {campaign_id} does not exist")
        records = (
            self.repository.list(IntentSignal, campaign_id=campaign_id)
            if campaign_id is not None
            else self.repository.list(IntentSignal)
        )
        return sorted(records, key=lambda item: (item.intent_score, item.captured_at), reverse=True)
