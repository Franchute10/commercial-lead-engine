"""Extract reviewable search phrases from visible website metadata and headings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup


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
    for source in (headings, (title,) if title else (), (description,) if description else ()):
        for phrase in source:
            for part in re.split(r"[|;•·]", phrase):
                candidate = _clean(part)
                words = candidate.split()
                if (
                    2 <= len(words) <= 8
                    and len(candidate) <= 90
                    and candidate.casefold() not in {item.casefold() for item in candidates}
                ):
                    candidates.append(candidate)
                if len(candidates) >= 12:
                    break
            if len(candidates) >= 12:
                break
        if len(candidates) >= 12:
            break

    return WebsiteSearchProfile(
        website=website,
        title=title,
        description=description,
        headings=headings[:12],
        keyword_candidates=tuple(candidates),
    )


def _clean(value: str) -> str:
    return " ".join(value.split())[:300]
