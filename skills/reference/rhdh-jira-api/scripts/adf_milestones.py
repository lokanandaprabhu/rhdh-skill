"""ADF milestone date extraction — shared across release skills.

Single home for parsing Jira ADF (Atlassian Document Format) milestone tables
from RHDHPLAN release Feature descriptions.  Both rhdh-release-schedule and
rhdh-release-fixversions import from here instead of carrying their own copies.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

MILESTONE_LABELS = {
    "feature_freeze": r"\bFeature Freeze\b",
    "code_freeze": r"\bCode Freeze\b",
    "doc_freeze": r"\bDocs? Freeze\b",
    "go_no_go": r"\bGo/No Go\b",
    "ga_announce": r"\bGA Announce\b",
}


def adf_text(node: dict[str, Any]) -> str:
    """Render the text and date values from an Atlassian Document Format node."""
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "date":
        try:
            timestamp = int(node.get("attrs", {}).get("timestamp"))
            return datetime.fromtimestamp(timestamp / 1000, timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError):
            return ""
    return " ".join(filter(None, (adf_text(child) for child in node.get("content", []))))


def adf_table_rows(node: dict[str, Any]) -> list[str]:
    """Return rendered rows from an ADF document's tables."""
    rows: list[str] = []
    if node.get("type") == "tableRow":
        rows.append(" | ".join(adf_text(cell).strip() for cell in node.get("content", [])))
    for child in node.get("content", []):
        rows.extend(adf_table_rows(child))
    return rows


def extract_milestone_dates(description: dict[str, Any] | str | None) -> dict[str, str]:
    """Parse the milestone table embedded in a release Feature description."""
    dates = {key: "TBD" for key in MILESTONE_LABELS}
    if isinstance(description, dict):
        lines = adf_table_rows(description)
    elif isinstance(description, str):
        lines = description.splitlines()
    else:
        return dates

    for line in lines:
        parsed_date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", line)
        if not parsed_date:
            continue
        for key, label_pattern in MILESTONE_LABELS.items():
            if re.search(label_pattern, line, re.IGNORECASE):
                dates[key] = parsed_date.group(0)
                break
    return dates
