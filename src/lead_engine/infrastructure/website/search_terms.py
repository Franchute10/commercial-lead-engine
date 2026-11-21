"""Extract reviewable search phrases from visible website metadata and headings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

_SERVICE_TERMS = (
    "air conditioning",
    "carpet cleaning",
    "window cleaning",
    "legal services",
    "tax preparation",
    "dental care",
    "pest control",
    "housekeeping",
    "cleaning",
    "roofing",
    "plumbing",
    "landscaping",
    "hvac",
    "repairs",
    "repair",
    "maintenance",
    "installations",
    "installation",
    "construction",
    "moving",
    "logistics",
    "marketing",
    "photography",
    "design",
    "dentistry",
    "painting",
    "services",
    "service",
)
_OFFER_VERBS = re.compile(
    r"\b(?:offers?|provides?|delivers?|specializes?\s+in|serves?|includes?)\b", re.I
)
_PROMO_WORDS = re.compile(r"\b(?:premium|professional|reliable|quality|leading|best)\b", re.I)
_COMPANY_SUFFIX = re.compile(
    r"\b(?:LLC|L\.L\.C\.?|INC\.?|CORP\.?|CORPORATION|LTD\.?|LIMITED)\b", re.I
)


@dataclass(frozen=True, slots=True)
class WebsiteSearchProfile:
    website: str
    title: str | None
    description: str | None
    headings: tuple[str, ...]
    keyword_candidates: tuple[str, ...]


def extract_website_search_profile(website: str, html: str) -> WebsiteSearchProfile:
    """Collect phrases visibly stated by the site; this does not infer service claims."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select(
        "script, style, template, noscript, nav, footer, [hidden], [aria-hidden='true']"
    ):
        element.decompose()

    title = _clean(soup.title.get_text(" ", strip=True)) if soup.title else None
    description_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = (
        _clean(str(description_tag.get("content", ""))) if description_tag else None
    )
    headings = tuple(
        dict.fromkeys(
            phrase
            for heading in soup.find_all(["h1", "h2", "h3"])
            if (phrase := _clean(heading.get_text(" ", strip=True)))
        )
    )

    candidates: list[str] = []
    for source in (headings, (title,) if title else ()):
        for phrase in source:
            for part in re.split(r"[|;•·]", phrase):
                candidate = _clean(part)
                words = candidate.split()
                if (
                    2 <= len(words) <= 8
                    and len(candidate) <= 90
                    and not _COMPANY_SUFFIX.search(candidate)
                    and candidate.casefold() not in {item.casefold() for item in candidates}
                ):
                    candidates.append(candidate)
                if len(candidates) >= 12:
                    break
            if len(candidates) >= 12:
                break
        if len(candidates) >= 12:
            break

    if description and len(candidates) < 12:
        candidates.extend(_service_phrases(description, limit=12 - len(candidates)))

    return WebsiteSearchProfile(
        website=website,
        title=title,
        description=description,
        headings=headings[:12],
        keyword_candidates=tuple(candidates),
    )


def _clean(value: str) -> str:
    return " ".join(value.split())[:300]


def _service_phrases(text: str, *, limit: int) -> list[str]:
    candidates: list[str] = []
    service_pattern = re.compile(
        r"\b(" + "|".join(re.escape(value) for value in _SERVICE_TERMS) + r")\b", re.I
    )
    for match in service_pattern.finditer(text):
        tail = text[match.end() : match.end() + 24]
        if _COMPANY_SUFFIX.match(tail.strip()):
            continue
        sentence = re.split(r"[.!?;|]", text[: match.start()])[-1]
        verbs = list(_OFFER_VERBS.finditer(sentence))
        if verbs:
            sentence = sentence[verbs[-1].end() :]
        sentence = _PROMO_WORDS.sub(" ", sentence)
        items = re.split(r",|\band\b|\b&\b", sentence, flags=re.I)
        for item in items:
            descriptor = _clean(re.sub(r"[^\w'/-]", " ", item, flags=re.UNICODE)).strip(" -")
            if not descriptor or _COMPANY_SUFFIX.search(descriptor):
                continue
            phrase = f"{descriptor} {match.group(1)}"
            words = phrase.split()
            if (
                len(words) < 2
                or len(words) > 6
                or len(phrase) > 70
                or phrase.casefold() in {value.casefold() for value in candidates}
            ):
                continue
            candidates.append(phrase)
            if len(candidates) >= limit:
                return candidates
    return candidates
