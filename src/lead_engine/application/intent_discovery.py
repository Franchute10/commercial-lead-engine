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
        r"\b(recommendations?|anyone know|who do you use|can anyone suggest|any suggestions|"
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
    r"\b(looking for|searching for|seeking|need(?:s)? (?:a|an|some|to hire)?|"
    r"want to hire|looking to hire|trying to find|looking to get|need help with|"
    r"can anyone recommend|can anyone suggest|anyone know a|anyone have recommendations|"
    r"does anyone have a recommendation|any recommendations|any suggestions|"
    r"recommendations? for|who do you use|what company do you use|"
    r"how much|request a quote|quote for|"
    r"estimate for|pricing for|price for|cost for|seeking an estimate|"
    r"busco|buscando|necesito|necesitamos|recomienden|alguien conoce|"
    r"alguna recomendaci[oó]n|recomendaciones? para)\b",
    re.IGNORECASE,
)
_PROVIDER_LANGUAGE = re.compile(
    r"\b(we offer|our services|call us|book now|serving [a-z]|now accepting clients|"
    r"new clients welcome|check out our|contact us|locally owned|family owned|"
    r"i own a|we are a|we're a|my company|our company|i am a professional|professional cleaner|"
    r"available for bookings|book your cleaning|ofrecemos|ll[aá]manos|"
    r"contrata ahora|nuevos clientes|agenda ahora)\b",
    re.IGNORECASE,
)
_PROMOTION_LANGUAGE = re.compile(
    r"\b(discount|special offer|coupon|promo code|limited time|free quote|"
    r"licensed and insured|#smallbusiness|#supportlocal)\b",
    re.IGNORECASE,
)
_EMPLOYMENT_LANGUAGE = re.compile(
    r"\b(part[- ]time|full[- ]time|contractor role|jobs? in [a-z]|"
    r"(?:house|home|office) ?cleaners? jobs?|housekeeper jobs?|"
    r"hiring (?:a |an )?(?:cleaner|housekeeper)|now hiring|we're hiring|"
    r"apply now|job description|job opening|careers|salary|hourly pay|per week)\b",
    re.IGNORECASE,
)
_SERVICE_ALIASES = {
    "house cleaning": (
        r"\b(house ?clean(?:ing|er|ers)?|home ?clean(?:ing|er|ers)?|"
        r"residential clean(?:ing|er|ers)?|cleaning crews?|cleaning teams?|"
        r"cleaning compan(?:y|ies)|maid service|housekeeping)\b"
    ),
    "home cleaning": (
        r"\b(house ?clean(?:ing|er|ers)?|home ?clean(?:ing|er|ers)?|"
        r"residential clean(?:ing|er|ers)?|cleaning crews?|cleaning teams?|"
        r"cleaning compan(?:y|ies)|maid service|housekeeping)\b"
    ),
    "cleaning": (
        r"\b(cleaning service|house ?clean(?:ing|er|ers)?|home ?clean(?:ing|er|ers)?|"
        r"residential clean(?:ing|er|ers)?|cleaning crews?|cleaning teams?|"
        r"cleaning compan(?:y|ies)|maid service|housekeeping)\b"
    ),
}
_SERVICE_QUERY_VARIANTS = {
    "house cleaning": (
        "house cleaning",
        "house cleaner",
        "cleaning crew",
        "maid service",
        "housekeeping",
    ),
    "home cleaning": (
        "home cleaning",
        "house cleaner",
        "cleaning crew",
        "maid service",
        "housekeeping",
    ),
    "cleaning": ("house cleaning", "house cleaner", "cleaning crew", "maid service"),
}
_SOCIAL_QUERY_VARIANTS = {
    # Keep the social search narrow: this exact vocabulary returned relevant
    # public Facebook posts in the manual Tavily check, while the wider query
    # pushed those posts out of the indexed results.
    "house cleaning": (
        "house cleaning",
        "house cleaner",
        "cleaning crew",
        "maid service",
        "housekeeping",
    ),
    "home cleaning": (
        "home cleaning",
        "house cleaner",
        "cleaning crew",
        "maid service",
        "housekeeping",
    ),
    "cleaning": ("house cleaning", "house cleaner", "maid service"),
}


class IntentDiscoveryService:
    """Search a public index, retain only request-like evidence, and score candidates."""

    def __init__(self, uow: UnitOfWork, provider: TavilySearchProvider | None = None) -> None:
        self.uow = uow
        self.repository = uow.repository
        self.provider = provider or TavilySearchProvider()
        self.signals = IntentSignalService(uow)
        self.rejection_counts: dict[str, int] = {}
        self.search_lane_counts: dict[str, int] = {}

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
        additional_terms: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
        show_filtered: bool = False,
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
        if len(zones) > 4:
            raise ValueError("Use at most four additional zones per discovery run")
        places = tuple(
            dict.fromkeys(
                value.strip()
                for value in (place, *zones)
                if value and value.strip()
            )
        )
        queries = _queries(
            service,
            places[0],
            search_lang,
            segment=segment,
            additional_terms=additional_terms,
            additional_places=places[1:],
        )
        saved: list[IntentSignal] = []
        seen: set[str] = set()
        ignored = 0
        self.rejection_counts = {
            "no_request_signal": 0,
            "no_buyer_context": 0,
            "service_mismatch": 0,
            "provider_or_promotion": 0,
            "employment_or_recruiting": 0,
            "duplicate": 0,
        }
        self.filtered_samples: list[dict[str, str]] = []

        def reject(reason: str, hit: SearchHit, text: str) -> None:
            self.rejection_counts[reason] += 1
            if show_filtered and len(self.filtered_samples) < 15:
                self.filtered_samples.append(
                    {
                        "reason": reason,
                        "title": hit.title[:240],
                        "url": hit.url,
                        "excerpt": text[:700],
                    }
                )

        lane_names = ("public_web", "facebook_groups", "nextdoor_public")
        self.search_lane_counts = {}

        for query_index, query in enumerate(queries):
            hits = self.provider.search(
                query,
                country=country_code,
                search_lang=search_lang,
                freshness=freshness,
                count=count,
            )
            lane_name = lane_names[query_index % len(lane_names)]
            self.search_lane_counts[lane_name] = (
                self.search_lane_counts.get(lane_name, 0) + len(hits)
            )
            for hit in hits:
                if hit.url in seen:
                    ignored += 1
                    reject("duplicate", hit, f"{hit.title} {hit.excerpt}".strip())
                    continue
                seen.add(hit.url)
                text = f"{hit.title} {hit.excerpt}".strip()
                request_type = _request_type(text)
                if request_type is None:
                    ignored += 1
                    reject("no_request_signal", hit, text)
                    continue
                # Request classification and buyer context are separate signals. Include
                # recommendation phrasing here so valid "who do you use?" posts survive.
                if not _BUYER_CONTEXT.search(text):
                    ignored += 1
                    reject("no_buyer_context", hit, text)
                    continue
                if not _service_matches(service, text):
                    ignored += 1
                    reject("service_mismatch", hit, text)
                    continue
                if _PROVIDER_LANGUAGE.search(text) or _PROMOTION_LANGUAGE.search(text):
                    ignored += 1
                    reject("provider_or_promotion", hit, text)
                    continue
                if _EMPLOYMENT_LANGUAGE.search(text):
                    ignored += 1
                    reject("employment_or_recruiting", hit, text)
                    continue
                posted_at = _parse_age(hit.page_age)
                city = next(
                    (matched for value in places if (matched := _place_match(value, text))),
                    None,
                )
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
                        eligible_places=places,
                    )
                except DuplicateError:
                    ignored += 1
                    self.rejection_counts["duplicate"] += 1
                    continue
                saved.append(record)
        return saved, ignored, len(queries)


def _queries(
    service: str,
    place: str,
    search_lang: str,
    segment: str | None = None,
    additional_terms: tuple[str, ...] = (),
    additional_places: tuple[str, ...] = (),
) -> list[str]:
    """Build source lanes using common service names and buyer-intent language."""
    qualifier = f'"{segment}" ' if segment else ""
    variants = _SERVICE_QUERY_VARIANTS.get(normalize_name(service), (service,))
    safe_terms = _safe_query_terms(additional_terms)
    variants = tuple(dict.fromkeys((*variants, *safe_terms[:3])))
    service_terms = " OR ".join(f'"{term}"' for term in variants)
    social_variants = _SOCIAL_QUERY_VARIANTS.get(normalize_name(service), variants)
    social_variants = tuple(dict.fromkeys((*social_variants, *safe_terms[:2])))
    social_service_terms = " OR ".join(f'"{term}"' for term in social_variants)
    intent = (
        '"looking for" OR need OR recommend OR recommendation OR "who do you use" OR quote'
        if search_lang.lower().startswith("en")
        else '"busco" OR necesito OR recomienden OR recomendación OR cotización'
    )
    queries: list[str] = []
    for search_place in dict.fromkeys((place, *additional_places)):
        core = f'({service_terms}) "{search_place}" {qualifier}({intent})'
        social_core = f'({social_service_terms}) "{search_place}" {qualifier}({intent})'
        queries.extend(
            (
                core,
                f'site:facebook.com/groups {social_core}',
                f'site:nextdoor.com {social_core}',
            )
        )
    return queries


def _safe_query_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    candidates: list[str] = []
    for term in terms:
        cleaned = re.sub(r"[^\w &'-]", " ", term, flags=re.UNICODE)
        cleaned = " ".join(cleaned.split()).strip(" -'&")[:45]
        if (
            2 <= len(cleaned.split()) <= 8
            and cleaned.casefold() not in {value.casefold() for value in candidates}
        ):
            candidates.append(cleaned)
    return tuple(candidates)

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
    normalized_place = normalize_name(place or "")
    normalized_text = normalize_name(text)
    if normalized_place and f" {normalized_place} " in f" {normalized_text} ":
        return place
    return None


def _platform(url: str) -> SignalPlatform:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("facebook.com") and "/groups/" in urlparse(url).path:
        return SignalPlatform.FACEBOOK_GROUP
    if host.endswith("nextdoor.com"):
        return SignalPlatform.NEXTDOOR
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
        parsed = parsed.astimezone(UTC)
        # Search indexes can expose malformed or future publication dates. Treat
        # those as unknown instead of letting them invalidate an otherwise useful
        # signal when it is captured.
        return None if parsed > datetime.now(UTC) else parsed
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
