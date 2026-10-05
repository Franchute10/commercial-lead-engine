"""CSV report serialization. Each row references its persisted run and source."""

import csv
from pathlib import Path

from lead_engine.domain.discovery import DiscoveryRun


def export_report(run: DiscoveryRun, path: Path) -> None:
    fields = [
        "run_id",
        "campaign_id",
        "provider",
        "run_status",
        "candidate_number",
        "row_number",
        "name",
        "external_id",
        "status",
        "company_id",
        "lead_id",
        "source_id",
        "company_created",
        "lead_created",
        "message",
    ]
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for outcome in run.outcomes:
            data = outcome.model_dump(mode="json", exclude={"id"})
            writer.writerow(
                {
                    key: safe_cell(value)
                    for key, value in {
                        "run_id": str(run.id),
                        "campaign_id": str(run.campaign_id),
                        "provider": run.provider,
                        "run_status": run.status.value,
                        **data,
                    }.items()
                }
            )


def safe_cell(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
