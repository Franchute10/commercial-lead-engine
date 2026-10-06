"""Deterministic text exports of saved snapshots, with no regeneration or network."""

import csv
import io
import json
from enum import StrEnum

from lead_engine.domain.research import CommercialBrief


class BriefFormat(StrEnum):
    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"


def _text(value: str) -> str:
    # Keep untrusted source text from injecting Markdown headings/links or HTML.
    value = " ".join(value.split())
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for token in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        value = value.replace(token, "\\" + token)
    return value


def markdown(brief: CommercialBrief) -> str:
    score = (
        f"{brief.latest_lead_score:g} / {brief.score_band.value if brief.score_band else 'unknown'}"
        if brief.latest_lead_score is not None
        else "Unknown"
    )
    lines = [
        f"# Commercial brief: {_text(brief.company_name)}",
        f"Company: {_text(brief.company_summary)}",
        f"Campaign: {_text(brief.campaign_name)}",
        f"Score: {score}",
        f"Research completeness: {brief.data_completeness}%",
        f"Status: {brief.status.value}",
        f"Priority: {brief.opportunity_priority.value}",
        f"Pipeline: {brief.pipeline_status.value}",
        f"Research version: {brief.research_version}",
        f"Generated: {brief.generated_at.isoformat()}",
        f"Brief ID: {brief.id}",
        "",
        "## Primary opportunity",
        "",
    ]
    if brief.primary_opportunity:
        lines.append(_text(brief.primary_opportunity.title))
        lines.extend(["", "### Why", ""])
        lines.extend(f"- {_text(reason)}" for reason in brief.primary_opportunity.reasons)
        lines.extend(
            [
                "",
                "Evidence: "
                + ", ".join(str(eid) for eid in brief.primary_opportunity.supporting_evidence_ids),
            ]
        )
    else:
        lines.append("No clear opportunity supported by the stored evidence.")
    if brief.secondary_opportunities:
        lines.extend(["", "## Secondary opportunities", ""])
        for opportunity in brief.secondary_opportunities:
            lines.append(
                f"- {_text(opportunity.title)}: " + "; ".join(_text(r) for r in opportunity.reasons)
            )
            lines.append(
                "  Evidence: " + ", ".join(str(eid) for eid in opportunity.supporting_evidence_ids)
            )
    lines.extend(["", "## Decision makers", ""])
    for index, contact in enumerate(brief.decision_maker_recommendations, 1):
        lines.append(
            f"{index}. {_text(contact.full_name)} — {_text(contact.current_role)} "
            f"— Fit {contact.fit_score} — {contact.confidence} — {contact.verification.value}"
        )
        for channel in contact.channels:
            lines.append(
                f"   - {channel.channel_type}: {_text(channel.value)} "
                f"(evidence {channel.evidence_id})"
            )
        for warning in contact.warnings:
            lines.append(f"   - Warning: {_text(warning)}")
        lines.append("   - Evidence: " + ", ".join(str(eid) for eid in contact.evidence_ids))
    if not brief.decision_maker_recommendations:
        lines.append(
            "No supported individual contact found. Target roles: "
            + ", ".join(r.value for r in brief.target_roles)
        )
    lines.extend(
        [
            "",
            "## Contact angle",
            "",
            _text(brief.suggested_contact_angle),
            "",
            "## Missing information",
            "",
        ]
    )
    lines.extend(f"- {_text(item)}" for item in brief.missing_information)
    if not brief.missing_information:
        lines.append("No tracked information gaps; human validation still required.")
    lines.extend(["", "## Completeness", ""])
    lines.extend(
        f"- {c.category}: {c.awarded_points}/{c.maximum_points} — {_text(c.explanation)}"
        for c in brief.completeness_categories
    )
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {_text(warning)}" for warning in brief.warnings)
    lines.extend(["", "## Source evidence", ""])
    for citation in brief.evidence_citations:
        lines.append(
            f"- {citation.evidence_id} — {_text(citation.evidence_type)} "
            f"— {_text(citation.statement)}"
        )
        lines.append(
            f"  Source: {citation.source_id}; URL: {_text(citation.source_url or 'not recorded')}; "
            f"title: {_text(citation.source_title or 'not recorded')}; "
            f"observed: {citation.observed_at.isoformat()}"
        )
    return "\n".join(lines) + "\n"


def _csv_cell(value: object) -> str:
    text = str(value) if value is not None else ""
    # Spreadsheet formula injection protection for stored/untrusted string fields.
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def export_briefs(briefs: list[CommercialBrief], format: BriefFormat) -> str:
    if format == BriefFormat.JSON:
        return (
            json.dumps([b.model_dump(mode="json") for b in briefs], ensure_ascii=False, indent=2)
            + "\n"
        )
    if format == BriefFormat.MARKDOWN:
        return "\n---\n\n".join(markdown(b) for b in briefs)
    buffer = io.StringIO(newline="")
    fields = [
        "brief_id",
        "lead_id",
        "company",
        "campaign",
        "research_version",
        "generated_at",
        "lead_score",
        "score_band",
        "score_current",
        "research_status",
        "priority",
        "completeness",
        "decision_maker_available",
        "primary_opportunity",
        "pipeline_status",
        "contact_angle",
    ]
    writer = csv.writer(buffer)
    writer.writerow(fields)
    for brief in briefs:
        values = [
            brief.id,
            brief.lead_id,
            brief.company_name,
            brief.campaign_name,
            brief.research_version,
            brief.generated_at.isoformat(),
            brief.latest_lead_score,
            brief.score_band.value if brief.score_band else None,
            brief.score_current,
            brief.status.value,
            brief.opportunity_priority.value,
            brief.data_completeness,
            brief.decision_maker_available,
            brief.opportunity_type.value,
            brief.pipeline_status.value,
            brief.suggested_contact_angle,
        ]
        writer.writerow([_csv_cell(value) for value in values])
    return buffer.getvalue()
