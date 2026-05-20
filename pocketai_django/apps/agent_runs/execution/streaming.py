from __future__ import annotations

import re

from apps.mcp.text.sanitizer import has_dsml_markup


def looks_like_machine_contract(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if has_dsml_markup(stripped):
        return True
    if lowered.startswith("```json"):
        lowered = lowered.removeprefix("```json").strip()
    elif lowered.startswith("```"):
        lowered = lowered.removeprefix("```").strip()
    contract_markers = (
        "\"run_report\"",
        "\"runreport\"",
        "\"memory_update\"",
        "\"memoryupdate\"",
        "\"notification_candidate\"",
        "\"notificationcandidate\"",
        "\"recommended_next_step\"",
        "\"recommendednextstep\"",
        "\"actions_taken\"",
        "\"actionstaken\"",
        "\"sources_covered\"",
        "\"sourcescovered\"",
        "\"touched_entities\"",
        "\"touchedentities\"",
        "\"rollback_notes\"",
        "\"rollbacknotes\"",
        "\"blockers\"",
        "\"artifacts\"",
        "\"approvals\"",
        "\"workflow_state\"",
        "\"workflowstate\"",
        "\"response_hash\"",
        "\"responsehash\"",
        "\"inspected_items\"",
        "\"inspecteditems\"",
    )
    if any(marker in lowered for marker in contract_markers):
        return True
    if "run report" in lowered or "run_report" in lowered:
        return True
    if "workflow_state" in lowered or "response_hash" in lowered:
        return True
    if lowered.startswith(("{", "[", "}", "]")) and re.search(r'"[a-zA-Z_][a-zA-Z0-9_]*"\s*:', lowered[:1200]):
        return True
    if re.match(r'^[}\]\s,]*"[a-zA-Z_][a-zA-Z0-9_]*"\s*:', stripped[:1200]):
        return True
    if re.match(r"""^[}\]\s,:'"]+[{[]""", stripped[:1200]):
        return True
    if re.match(r"^\s*(?:null|true|false|\d+)\s*,", stripped[:400], re.IGNORECASE):
        return True
    if re.match(r'^\s*"[^"]{1,2000}"\s*,\s*(?:"|null|true|false|\d+|[{\[])', stripped[:2200], re.IGNORECASE | re.DOTALL):
        return True
    if re.search(r'^\s*["\'](?:completed|no_change|changed|failed|ok)["\']\s*,', stripped[:400], re.IGNORECASE | re.MULTILINE):
        return True
    if stripped.count("{") + stripped.count("[") + stripped.count('",') >= 4 and re.search(
        r'"(?:status|identity|state|tool|findings|actions_taken|notification_candidate)"',
        lowered,
    ):
        return True
    return False
