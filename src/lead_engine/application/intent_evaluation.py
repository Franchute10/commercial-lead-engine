"""Local, append-only discovery evidence and human evaluation; never contacts sources."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

LABELS = {"REAL_REQUEST", "NOT_REQUEST", "PROVIDER", "JOB", "WRONG_AREA", "STALE", "UNCLEAR"}


def url_key(url: str) -> str:
    return urldefrag(url.strip())[0].rstrip("/")


class RunJournal:
    """Exclusive file per run. Failed runs remain inspectable and never claim DB success."""

    def __init__(self, path: Path, configuration: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.append({"event": "start", "schema_version": 1, "configuration": configuration})

    def append(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"recorded_at": datetime.now(UTC).isoformat(), **event}, ensure_ascii=False
                )
                + "\n"
            )


def read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Expected JSON objects in the run journal")
        events.append(value)
    if not events or events[0].get("event") != "start":
        raise ValueError("Not an intent run journal")
    return events


def label_result(path: Path, url: str, label: str, note: str = "") -> None:
    if label not in LABELS:
        raise ValueError("Label must be one of: " + ", ".join(sorted(LABELS)))
    events = read_events(path)
    if not any(e.get("event") == "result" and url_key(e["url"]) == url_key(url) for e in events):
        raise ValueError("URL was not observed in this run")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "event": "label",
                    "url": url,
                    "label": label,
                    "note": note,
                    "recorded_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def evaluate(path: Path, truth_path: Path | None = None) -> dict[str, Any]:
    events = read_events(path)
    rows = [e for e in events if e.get("event") == "result"]
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(url_key(row["url"]), row)
    candidates = {
        url_key(e["url"]) for e in rows if e["outcome"] == "candidate" or e.get("filter_passed")
    }
    labels = {url_key(e["url"]): e["label"] for e in events if e.get("event") == "label"}
    judged = {u for u in candidates if u in labels and labels[u] != "UNCLEAR"}
    true_positive = {u for u in judged if labels[u] == "REAL_REQUEST"}
    false_negative = {
        u for u, label in labels.items() if label == "REAL_REQUEST" and u not in candidates
    }
    queries = []
    for q in (e for e in events if e.get("event") == "query"):
        hits = [e for e in rows if e["query_index"] == q["query_index"]]
        queries.append(
            {**q, "results": len(hits), "outcomes": dict(Counter(e["outcome"] for e in hits))}
        )
    completed = any(e.get("event") == "complete" for e in events)
    lanes = {}
    for lane in dict.fromkeys(q["lane"] for q in queries):
        hits = [e for e in rows if e.get("lane") == lane]
        urls = {url_key(e["url"]) for e in hits}
        lane_candidates = urls & candidates
        lane_judged = lane_candidates & judged
        lanes[lane] = {
            "results": len(hits),
            "unique_urls": len(urls),
            "candidate_urls": len(lane_candidates),
            "labelled_candidate_urls": len(lane_judged),
            "precision_on_labelled_candidates": len(lane_judged & true_positive) / len(lane_judged)
            if lane_judged
            else None,
            "outcomes": dict(Counter(e["outcome"] for e in hits)),
        }
    result: dict[str, Any] = {
        "configuration": events[0]["configuration"],
        "status": "COMPLETE" if completed else "INCOMPLETE",
        "database_committed": completed,
        "queries_attempted": len(queries),
        "results": len(rows),
        "unique_urls": len(unique),
        "candidate_urls": len(candidates),
        "labelled_candidate_urls": len(judged),
        "unlabelled_or_unclear_candidates": len(candidates - judged),
        "precision_on_labelled_candidates": len(true_positive) / len(judged) if judged else None,
        "human_identified_missed_requests": sorted(false_negative),
        "date_coverage": sum(bool(e.get("parsed_date")) for e in unique.values()) / len(unique)
        if unique
        else None,
        "geo_coverage": sum(bool(e.get("geo_match")) for e in unique.values()) / len(unique)
        if unique
        else None,
        "outcomes": dict(Counter(e["outcome"] for e in rows)),
        "queries": queries,
        "lanes": lanes,
        "verified_contactable_leads": None,
        "note": "Index evidence is not human verification. Unknown metrics are null, not zero.",
    }
    if truth_path:
        truth_data = json.loads(truth_path.read_text(encoding="utf-8"))
        if not isinstance(truth_data, list) or any(not isinstance(u, str) for u in truth_data):
            raise ValueError("Truth file must be a JSON list of manually verified source URLs")
        truth = {url_key(u) for u in truth_data}
        result["manual_truth_urls"] = len(truth)
        result["retrieval_recall_on_manual_sample"] = (
            len(truth & unique.keys()) / len(truth) if truth else None
        )
        result["candidate_recall_on_manual_sample"] = (
            len(truth & candidates) / len(truth) if truth else None
        )
        result["missed_manual_urls"] = sorted(truth - unique.keys())
    return result
