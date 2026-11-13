"""Discover explicit service requests from indexed public web pages."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID

from lead_engine.application.intent_signals import IntentSignalService
from lead_engine.application.ports import UnitOfWork
from lead_engine.domain.errors import DuplicateError
from lead_engine.domain.identity import normalize_name
from lead_engine.domain.intent_signal import IntentSignal, RequestType, SignalPlatform
from lead_engine.domain.models import Campaign
from lead_engine.infrastructure.public_search import TavilySearchProvider, SearchHit


_REQUEST_PATTERNS = {
    RequestType.QUOTE_REQUEST: re.compile(
        r"\b(quote|estimate|pricing|how much|cost|presupuesto|cotizaci[oó]n|cu[aá]nto cuesta)\b",
        re.IGNORECASE,
    ),
    RequestType.RECOMMENDATION_REQUEST: re.compile(
        r"\b(recommend|recommendation|anyone know|who do you use|can anyone suggest|"
        r"recomienden|recomendaci[oó]n|alguien conoce|alguna recomendaci[oó]n)\b",
        re.IGNORECASE,
    ),
    RequestType.SERVICE_REQUEST: re.compile(
        r"\b(looking for|searching for|seeking|need(?:s)?|want to hire|hiring|"
        r"busco|buscando|necesito|necesitamos|requiero)\b",
        re.IGNORECASE,
    ),
}
_BUYER_CONTEXT = re.compile(
    r"\b(i am looking for|i'm looking for|looking for|need(?:s)? (?:a|an|some|to hire)?|"
    r"want to hire|trying to find|can anyone recommend|anyone know a|how much|"
    r"busco|buscando|necesito|necesitamos|recomienden|alguien conoce)\b",
    re.IGNORECASE,
)
_PROVIDER_LANGUAGE = re.compile(
    r"\b(we offer|our services|call us|book now|serving [a-z]|now accepting clients|"
    r"new clients welcome|check out our|contact us|locally owned|family owned|"
    r"i own a|we are a|we're a|my company|our company|ofrecemos|ll[aá]manos|"
    r"contrata ahora|nuevos clientes|agenda ahora)\b",
    re.IGNORECASE,
)
_PROMOTION_LANGUAGE = re.compile(
    r"\b(discount|special offer|coupon|promo code|limited time|free quote|"
    r"licensed and insured|#smallbusiness|#supportlocal)\b",
    re.IGNORECASE,
)
_SERVICE_ALIASES = {
    "house cleaning": (
        r"\b(house ?clean(?:ing|er|ers)?|home ?clean(?:ing|er|ers)?|"
        r"residential clean(?:ing|er|ers)?|maid service|housekeeping)\b"
    ),
    "home cleaning": (
        r"\b(house ?clean(?:ing|er|ers)?|home ?clean(?:ing|er|ers)?|"
        r"residential clean(?:ing|er|ers)?|maid service|housekeeping)\b"
    ),
    "cleaning": (
        r"\b(cleaning service|house ?clean(?:ing|er|ers)?|home ?clean(?:ing|er|ers)?|"
        r"residential clean(?:ing|er|ers)?|maid service|housekeeping)\b"
    ),
}


class IntentDiscoveryService:
    """Search a public index, retain only request-like evidence, and score candidates."""

    def __init__(self, uow: UnitOfWork, provider: TavilySearchProvider | None = None) -> None:
        self.uow = uow
        self.repository = uow.repository
        self.provider = provider or TavilySearchProvider()
        self.signals = IntentSignalService(uow)

    def discover(
        self,
        *,
        campaign_id: UUID,
        service: str,
        country_code: str = "US",
        search_lang: str = "en",
        count: int = 10,
        freshness: str = "pm",
        segment: str | None = None,
    ) -> tuple[list[IntentSignal], int, int]:
        campaign = self.repository.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign {campaign_id} does not exist")
        place = campaign.city or campaign.region or campaign.country or campaign.geography
        if not place:
            raise ValueError("Set a campaign city, region, country, or geography before searching")
        service = service.strip()
        if not service:
            raise ValueError("Provide the service to search for")
        queries = _queries(service, place, search_lang, segment=segment)
        saved: list[IntentSignal] = []
        seen: set[str] = set()
        ignored = 0

        for query in queries:
            for hit in self.provider.search(
                query,
                country=country_code,
                search_lang=search_lang,
                freshness=freshness,
                count=count,
            ):
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                text = f"{hit.title} {hit.excerpt}".strip()
                request_type = _request_type(text)
                if (
                    request_type is None
                    or not _BUYER_CONTEXT.search(text)
                    or not _service_matches(service, text)
                    or _PROVIDER_LANGUAGE.search(text)
                    or _PROMOTION_LANGUAGE.search(text)
                ):
                    ignored += 1
                    continue
                posted_at = _parse_age(hit.page_age)
                city = _place_match(campaign.city, text)
                region = _place_match(campaign.region, text)
                country = _place_match(campaign.country, text)
                platform = _platform(hit.url)
                try:
                    record = self.signals.capture(
                        campaign_id=campaign_id,
                        platform=platform,
                        source_url=hit.url,
                        request_type=request_type,
                        service_requested=service,
                        evidence_excerpt=text[:1000],
                        city=city,
                        region=region,
                        country=country,
                        posted_at=posted_at,
                    )
                except DuplicateError:
                    ignored += 1
                    continue
                saved.append(record)
        return saved, ignored, len(queries)


def _queries(service: str, place: str, search_lang: str, segment: str | None = None) -> list[str]:
    """Build separate, source-specific lanes for an explicit service and market segment."""
    qualifier = f'"{segment}" ' if segment else ""
    intent = (
        '("need" OR "looking for" OR "recommend" OR "anyone know")'
        if search_lang.lower().startswith("en")
        else '("necesito" OR "busco" OR "recomienden" OR "alguien conoce")'
    )
    core = f'"{service}" "{place}" {qualifier}{intent}'
    return [
        core,
        f'site:facebook.com/groups {core}',
        f'site:reddit.com {core}',
    ]

def _request_type(text: str) -> RequestType | None:
    for request_type in (
        RequestType.QUOTE_REQUEST,
        RequestType.RECOMMENDATION_REQUEST,
        RequestType.SERVICE_REQUEST,
    ):
        if _REQUEST_PATTERNS[request_type].search(text):
            return request_type
    return None


def _service_matches(service: str, text: str) -> bool:
    normalized_service = normalize_name(service)
    normalized_text = normalize_name(text)
    alias = _SERVICE_ALIASES.get(normalized_service)
    if alias:
        return re.search(alias, normalized_text, re.IGNORECASE) is not None

    terms = [term for term in normalized_service.split() if len(term) > 2]
    if not terms:
        return False
    # A multiword service must match as a phrase; one generic token is insufficient.
    return normalized_service in normalized_text if len(terms) > 1 else bool(
        re.search(rf"\b{re.escape(terms[0])}\b", normalized_text)
    )

def _place_match(place: str | None, text: str) -> str | None:
    if place and normalize_name(place) in normalize_name(text):
        return place
    return None


def _platform(url: str) -> SignalPlatform:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("facebook.com") and "/groups/" in urlparse(url).path:
        return SignalPlatform.FACEBOOK_GROUP
    if host.endswith("reddit.com"):
        return SignalPlatform.REDDIT
    return SignalPlatform.PUBLIC_WEB


def _parse_age(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        pass
    match = re.fullmatch(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", normalized, re.I)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    delta = {
        "minute": timedelta(minutes=amount),
        "hour": timedelta(hours=amount),
        "day": timedelta(days=amount),
        "week": timedelta(weeks=amount),
        "month": timedelta(days=30 * amount),
    }[unit]
    return datetime.now(UTC) - delta
