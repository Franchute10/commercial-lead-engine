"""Local review exports; no clipboard or transport integration."""

import json
from enum import StrEnum

from lead_engine.domain.outreach import DraftView


class OutreachFormat(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"


def export_drafts(views: list[DraftView], format: OutreachFormat) -> str:
    if format == OutreachFormat.JSON:
        return (
            json.dumps([v.model_dump(mode="json") for v in views], ensure_ascii=False, indent=2)
            + "\n"
        )
    sections = []
    for view in views:
        d = view.draft
        lines = [
            f"Draft: {d.id}",
            f"Company: {d.company_name}",
            f"Contact: {d.contact_name} ({d.contact_role})",
            f"Channel: {d.channel} / {d.channel_value}",
            f"Purpose: {d.purpose}",
            f"Version: {d.outreach_version}",
            f"Status: {view.status}",
            "Human review required. Approval does not send.",
            "",
        ]
        if d.subject:
            lines += [f"Subject: {d.subject}", ""]
        lines += [d.body, "", "Evidence:"]
        lines += [
            (
                f"- {c.evidence_id} | source {c.source_id} | "
                f"{c.source_url or 'URL not recorded'} | "
                f"{c.observed_at.isoformat()} | {c.statement}"
            )
            for c in d.evidence_citations
        ]
        lines += ["", "Warnings:", *[f"- {w}" for w in d.warnings]]
        text = "\n".join(lines)
        if format == OutreachFormat.MARKDOWN:
            # Escape all user-controlled Markdown/HTML; no active links or embedded images.
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            for character in ("\\", "`", "*", "_", "[", "]", "#", "!", "|"):
                text = text.replace(character, "\\" + character)
            text = "## Outreach draft\n\n" + text.replace("\n", "  \n")
        sections.append(text)
    return "\n\n".join(sections) + "\n"
