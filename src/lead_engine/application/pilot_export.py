"""Export the reviewed cohort and explicitly defined human-label summaries."""

import csv
import io
import json
from enum import StrEnum

from lead_engine.domain.pilot import PilotReport


class PilotFormat(StrEnum):
    MARKDOWN = "markdown"
    CSV = "csv"
    JSON = "json"


def safe_text(value: str) -> str:
    value = " ".join(value.split())
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for token in ("\\", "`", "*", "_", "[", "]", "#", "|", "!"):
        value = value.replace(token, "\\" + token)
    return value


def export_report(report: PilotReport, format: PilotFormat) -> str:
    if format == PilotFormat.JSON:
        return report.model_dump_json(indent=2) + "\n"
    if format == PilotFormat.CSV:
        return csv_report(report)
    precision = (
        "N/A"
        if report.shortlist_precision_percent is None
        else f"{report.shortlist_precision_percent:g}%"
    )
    contact = (
        "N/A" if report.would_contact_percent is None else f"{report.would_contact_percent:g}%"
    )
    lines = [
        f"# Pilot evaluation: {safe_text(report.campaign_name)}",
        "",
        f"Evaluator: {safe_text(report.evaluator)}",
        f"Shortlist run: {report.shortlist_run_id}",
        f"Shortlist generated: {report.shortlist_generated_at.isoformat()}",
        f"Report generated: {report.generated_at.isoformat()}",
        "",
        f"Shortlisted companies: {report.shortlisted_companies}",
        f"Companies evaluated: {report.companies_evaluated}",
        f"Unevaluated: {len(report.unevaluated_lead_ids)}",
        (
            f"Shortlist precision (YES / YES+NO): {precision}; "
            f"denominator={report.precision_denominator}"
        ),
        f"% {safe_text(report.evaluator)} would contact (YES / all evaluated): {contact}",
        "MAYBE is unresolved; unevaluated leads are excluded from both denominators.",
        "Human contact-selection agreement only; no sales, recall or response-rate claim.",
        "",
        f"False positives (would_contact=NO): {len(report.false_positive_lead_ids)}",
        f"Leads needing more research: {len(report.leads_needing_more_research)}",
        "",
    ]
    for name, distribution in (
        ("Would contact", report.would_contact_distribution),
        ("Decision-maker quality", report.decision_maker_quality_distribution),
        ("Opportunity quality", report.opportunity_quality_distribution),
        ("Outreach quality", report.outreach_quality_distribution),
    ):
        lines.append(
            name + ": " + ", ".join(f"{label}={count}" for label, count in distribution.items())
        )
    lines += [
        "",
        "| Company / lead | Contact? | Decision maker | Opportunity | Outreach | Research? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report.rows:
        e = row.evaluation
        labels = (
            [e.would_contact, e.decision_maker_quality, e.opportunity_quality, e.outreach_quality]
            if e
            else ["UNEVALUATED"] * 4
        )
        lines.append(
            f"| {safe_text(row.item.company_name)} / {row.item.lead_id} | "
            + " | ".join(str(value) for value in labels)
            + f" | {'YES' if row.needs_more_research else 'NO'} |"
        )
    lines += ["", "## Evaluation notes and artifacts", ""]
    for row in report.rows:
        e = row.evaluation
        if e:
            lines += [
                "",
                f"Evaluation: {e.id}; revision={e.revision}; at={e.evaluated_at.isoformat()}",
                (
                    f"Score: {e.score_id}; brief: {e.commercial_brief_id}; "
                    f"contact: {e.recommended_contact_id}; "
                    f"draft: {e.outreach_draft_id or 'NOT AVAILABLE'}"
                ),
            ]
            if e.evaluator_notes:
                lines.append("Notes: " + safe_text(e.evaluator_notes))
            lines.append("")
    return "\n".join(lines) + "\n"


def csv_report(report: PilotReport) -> str:
    buffer = io.StringIO(newline="")
    fields = [
        "campaign_id",
        "campaign_name",
        "shortlist_run_id",
        "evaluator",
        "generated_at",
        "shortlisted_companies",
        "companies_evaluated",
        "unevaluated_count",
        "shortlist_precision_percent",
        "precision_denominator",
        "would_contact_percent",
        "false_positive_count",
        "needs_more_research_count",
        "would_contact_distribution",
        "decision_maker_quality_distribution",
        "opportunity_quality_distribution",
        "outreach_quality_distribution",
        "lead_id",
        "company_name",
        "next_action",
        "evaluation_id",
        "revision",
        "evaluated_at",
        "would_contact",
        "decision_maker_quality",
        "opportunity_quality",
        "outreach_quality",
        "evaluator_notes",
        "score_id",
        "commercial_brief_id",
        "recommended_contact_id",
        "outreach_draft_id",
        "needs_more_research",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    summary: dict[str, object] = {name: getattr(report, name) for name in fields[:7]}
    summary.update(
        {
            "generated_at": report.generated_at.isoformat(),
            "unevaluated_count": len(report.unevaluated_lead_ids),
            "shortlist_precision_percent": report.shortlist_precision_percent,
            "precision_denominator": report.precision_denominator,
            "would_contact_percent": report.would_contact_percent,
            "false_positive_count": len(report.false_positive_lead_ids),
            "needs_more_research_count": len(report.leads_needing_more_research),
        }
    )
    for name in fields[13:17]:
        summary[name] = json.dumps(getattr(report, name), ensure_ascii=False)
    rows: list[dict[str, object]] = []
    for row in report.rows:
        e = row.evaluation
        values = {
            **summary,
            "lead_id": str(row.item.lead_id),
            "company_name": row.item.company_name,
            "next_action": row.item.recommended_next_action.value,
            "needs_more_research": row.needs_more_research,
        }
        if e:
            values.update(e.model_dump(exclude={"id", "company_id", "evaluation_version"}))
            values["evaluation_id"] = str(e.id)
            values["evaluated_at"] = e.evaluated_at.isoformat()
        rows.append(values)
    for values in rows or [summary]:
        writer.writerow(
            {
                key: (
                    "'" + value
                    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                    else value
                )
                for key, value in values.items()
                if key in fields
            }
        )
    return buffer.getvalue()
