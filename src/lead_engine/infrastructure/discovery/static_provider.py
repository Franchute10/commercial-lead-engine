"""Fixture-backed public-search demo. Does not fetch or claim to run a live search."""

import json
from collections.abc import Iterable
from pathlib import Path

from lead_engine.application.discovery import DiscoveryCandidate, DiscoveryQuery
from lead_engine.domain.enums import SourceType


class StaticDiscoveryProvider:
    name = "static"
    source_type = SourceType.OTHER

    def __init__(self, candidates: Iterable[DiscoveryCandidate]) -> None:
        self._candidates = tuple(candidates)

    @classmethod
    def from_file(cls, path: Path) -> "StaticDiscoveryProvider":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Search fixture must be a JSON array of candidate objects")
        candidates = [DiscoveryCandidate.model_validate(item) for item in data]
        candidates = [
            candidate.model_copy(
                update={
                    "metadata": {
                        **candidate.metadata,
                        "fixture_file": str(path.resolve()),
                        "demo": True,
                    }
                }
            )
            for candidate in candidates
        ]
        return cls(candidates)

    def discover(self, query: DiscoveryQuery) -> Iterable[DiscoveryCandidate]:
        # Query context is recorded, not used to invent locality or imply a real search.
        return self._candidates[: query.limit]
