"""Bounded public-web search client for intent discovery."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

_COUNTRY_NAMES = {
    "US": "United States",
    "PE": "Peru",
    "CA": "Canada",
    "MX": "Mexico",
    "GB": "United Kingdom",
    "AU": "Australia",
    "NZ": "New Zealand",
    "IE": "Ireland",
    "ES": "Spain",
}


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    excerpt: str
    page_age: str | None
    result_type: str


class TavilySearchProvider:
    """Search Tavily's public web index; never fetches or logs the API credential."""

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 10.0) -> None:
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
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
            raise ValueError("Set TAVILY_API_KEY to use public-web discovery")
        if not query.strip():
            raise ValueError("Search query cannot be blank")
        if len(query) > 400:
            raise ValueError("Search query must be 400 characters or fewer")
        if not 1 <= count <= 20:
            raise ValueError("Search count must be between 1 and 20")

        time_range = {
            "pd": "day",
            "pw": "week",
            "pm": "month",
            "py": "year",
            "day": "day",
            "week": "week",
            "month": "month",
            "year": "year",
        }.get(freshness.lower())
        if time_range is None:
            raise ValueError("Freshness must be pd, pw, pm, py, day, week, month, or year")

        # Tavily accepts a country name, rather than the two-letter search codes
        # used by this CLI. Unknown codes are omitted; campaign location remains
        # in the generated search query.
        country_name = _COUNTRY_NAMES.get(country.upper())
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": count,
            "time_range": time_range,
            "include_answer": False,
            "include_raw_content": False,
        }
        if "site:facebook.com/groups" in query:
            payload["include_domains"] = ["facebook.com"]
        elif "site:reddit.com" in query:
            payload["include_domains"] = ["reddit.com"]
        if country_name:
            payload["country"] = country_name

        try:
            response = httpx.post(
                TAVILY_SEARCH_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as exc:
            raise OSError(
                f"Public search request failed: HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OSError(
                f"Public search request failed: {exc.__class__.__name__}"
            ) from exc

        if not isinstance(result, Mapping):
            raise OSError("Public search returned an invalid response")
        results = result.get("results")
        if not isinstance(results, list):
            raise OSError("Public search response did not contain results")

        hits: list[SearchHit] = []
        for item in results:
            if not isinstance(item, Mapping):
                continue
            title = _text(item.get("title"))
            url = _text(item.get("url"))
            excerpt = _text(item.get("content"))
            if not url:
                continue
            hits.append(
                SearchHit(
                    title=title or url,
                    url=url,
                    excerpt=excerpt[:1000],
                    page_age=_optional_text(item.get("published_date")),
                    result_type="web",
                )
            )
        return hits


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    result = _text(value)
    return result or None
