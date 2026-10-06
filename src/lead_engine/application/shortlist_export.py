"""Snapshot exports: console, Markdown, JSON and spreadsheet-safe CSV."""

import csv
import io
import json
from collections import Counter
from enum import StrEnum

from lead_engine.domain.shortlist import DailyShortlistRun, ShortlistDecision


class ShortlistFormat(StrEnum):
    CONSOLE = "console"
    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"


def safe_text(value: str, markdown: bool = False) -> str:
    value = " ".join(value.split())
    if markdown:
        value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        for token in ("\\", "`", "*", "_", "[", "]", "#", "|"):
            value = value.replace(token, "\\" + token)
    return value


def render_run(run: DailyShortlistRun, markdown: bool = False) -> str:
    lines = [
        ("# " if markdown else "") + "DAILY SHORTLIST",
        run.generated_at.date().isoformat(),
        f"Run: {run.id}; policy: {run.policy_version}",
        "Human review required; actions are recommendations only.",
        "",
    ]
    for item in run.items:
        score = (
            f"{item.latest_score:g} / {item.score_band.value}"
            if item.latest_score is not None and item.score_band
            else "unknown"
        )
        contact_name = safe_text(item.recommended_contact_name or "not identified", markdown)
        contact_role = safe_text(item.recommended_contact_role or "target roles", markdown)
        contact_fit = (
            item.recommended_contact_fit if item.recommended_contact_fit is not None else "unknown"
        )
        research_status = item.research_status.value if item.research_status else "missing"
        score_complete = (
            item.score_completeness if item.score_completeness is not None else "unknown"
        )
        lines.extend(
            [
                f"{item.shortlist_rank}. {safe_text(item.company_name, markdown)}",
                f"   Campaign: {safe_text(item.campaign_name, markdown)} "
                f"/ {item.campaign_type.value}",
                f"   Lead Score: {score}; score completeness: {score_complete}",
                f"   Shortlist Priority: {item.shortlist_priority_score}",
                f"   Opportunity: {item.primary_opportunity.value}; "
                f"priority: {item.opportunity_priority.value}",
                f"   Research: {research_status} / {item.research_completeness}%",
                f"   Pipeline: {item.pipeline_status.value}",
                f"   Contact: {contact_name} — {contact_role} — Fit {contact_fit}",
                f"   Channel: {item.recommended_channel or 'none'}: "
                f"{safe_text(item.recommended_channel_value or '', markdown)}",
                f"   Next action: {item.recommended_next_action.value}",
                f"   Angle: {safe_text(item.suggested_contact_angle, markdown)}",
                "",
                "   Why now:",
            ]
        )
        lines.extend(f"   - {safe_text(reason, markdown)}" for reason in item.reasons)
        lines.extend(f"   - Warning: {safe_text(warning, markdown)}" for warning in item.warnings)
        lines.append("   Evidence: " + ", ".join(str(eid) for eid in item.evidence_ids))
        lines.append("")
    if not run.items:
        lines.append("No eligible leads matched the current rules and filters.")
    counts = Counter(
        reason.value for decision in run.suppressed for reason in decision.suppression_reasons
    )
    lines.extend(
        [
            f"Considered: {run.candidates_considered}; "
            f"suppressed: {run.candidates_suppressed}; "
            f"returned: {run.items_returned}; "
            f"eligible beyond limit: {len(run.eligible_not_selected)}",
            "Suppression reasons: "
            + (", ".join(f"{key}={count}" for key, count in sorted(counts.items())) or "none"),
            "Use shortlist explain --lead-id UUID for current decisions; "
            "JSON history retains suppressed snapshots.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_decision(decision: ShortlistDecision) -> str:
    item = decision.item
    lines = [
        f"Lead: {item.lead_id} — {safe_text(item.company_name)}",
        f"Shortlist priority: {item.shortlist_priority_score}; "
        f"next action: {item.recommended_next_action.value}",
        "Suppressed: "
        + (", ".join(reason.value for reason in decision.suppression_reasons) or "no"),
        "Eligible again: "
        + (
            item.eligible_again_at.isoformat() if item.eligible_again_at else "no outbound cooldown"
        ),
    ]
    lines.extend(f"- {safe_text(reason)}" for reason in item.reasons)
    lines.extend(f"- Warning: {safe_text(warning)}" for warning in item.warnings)
    return "\n".join(lines) + "\n"


def export_runs(runs: list[DailyShortlistRun], format: ShortlistFormat) -> str:
    if format == ShortlistFormat.JSON:
        return (
            json.dumps([run.model_dump(mode="json") for run in runs], ensure_ascii=False, indent=2)
            + "\n"
        )
    if format in {ShortlistFormat.CONSOLE, ShortlistFormat.MARKDOWN}:
        return "\n".join(render_run(run, format == ShortlistFormat.MARKDOWN) for run in runs)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "run_id",
            "generated_at",
            "policy_version",
            "rank",
            "lead_id",
            "company",
            "campaign",
            "lead_score",
            "score_band",
            "score_completeness",
            "shortlist_priority",
            "research_status",
            "research_completeness",
            "opportunity",
            "opportunity_priority",
            "pipeline_status",
            "contact",
            "contact_role",
            "contact_fit",
            "channel",
            "channel_value",
            "next_action",
            "last_interaction_at",
            "contact_angle",
        ]
    )
    for run in runs:
        for item in run.items:
            values = [
                run.id,
                run.generated_at.isoformat(),
                run.policy_version,
                item.shortlist_rank,
                item.lead_id,
                item.company_name,
                item.campaign_name,
                item.latest_score,
                item.score_band.value if item.score_band else None,
                item.score_completeness,
                item.shortlist_priority_score,
                item.research_status.value if item.research_status else None,
                item.research_completeness,
                item.primary_opportunity.value,
                item.opportunity_priority.value,
                item.pipeline_status.value,
                item.recommended_contact_name,
                item.recommended_contact_role,
                item.recommended_contact_fit,
                item.recommended_channel,
                item.recommended_channel_value,
                item.recommended_next_action.value,
                item.last_interaction_at.isoformat() if item.last_interaction_at else None,
                item.suggested_contact_angle,
            ]
            cells = []
            for value in values:
                text = str(value) if value is not None else ""
                cells.append("'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text)
            writer.writerow(cells)
    return buffer.getvalue()
