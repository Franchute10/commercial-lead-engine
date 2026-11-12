"""Bounded public-web search client for intent discovery."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    excerpt: str
    page_age: str | None
    result_type: str


class BraveSearchProvider:
    """Search Brave's public index; never fetches or logs the API credential."""

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 10.0) -> None:
        self.api_key = api_key or os.getenv("BRAVE_SEARCH_API_KEY")
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        query: str,
        *,
        country: str,
        search_lang: str,
        freshness: str = "pm",
        count: int = 10,
    ) -> list[SearchHit]:
        if not self.api_key:
            raise ValueError("Set BRAVE_SEARCH_API_KEY to use public-web discovery")
        if not query.strip():
            raise ValueError("Search query cannot be blank")
        if not 1 <= count <= 20:
            raise ValueError("Search count must be between 1 and 20")
        try:
            response = httpx.get(
                BRAVE_SEARCH_URL,
                params={
                    "q": query,
                    "country": country.upper(),
                    "search_lang": search_lang.lower(),
                    "freshness": freshness,
                    "count": count,
                    "result_filter": "discussions,web",
                    "extra_snippets": "true",
                },
                headers={"X-Subscription-Token": self.api_key},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise OSError(f"Public search request failed: {exc.__class__.__name__}") from exc
        if not isinstance(payload, Mapping):
            raise OSError("Public search returned an invalid response")

        hits: list[SearchHit] = []
        for section_name in ("discussions", "web"):
            section = payload.get(section_name)
            if not isinstance(section, Mapping):
                continue
            results = section.get("results")
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, Mapping):
                    continue
                title = _text(item.get("title"))
                url = _text(item.get("url"))
                excerpt = _text(item.get("description"))
                if not url:
                    continue
                extra = item.get("extra_snippets")
                if isinstance(extra, list):
                    excerpt = " ".join(
                        part for part in [excerpt, *(_text(value) for value in extra)] if part
                    )
                hits.append(
                    SearchHit(
                        title=title or url,
                        url=url,
                        excerpt=excerpt[:1000],
                        page_age=_optional_text(item.get("page_age"))
                        or _optional_text(item.get("age")),
                        result_type=section_name,
                    )
                )
        return hits


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    result = _text(value)
    return result or None
