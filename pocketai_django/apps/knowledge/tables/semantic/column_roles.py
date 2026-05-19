from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


COLUMN_ROLE_DESCRIPTOR = "descriptor"
COLUMN_ROLE_QUALIFIER = "qualifier"
COLUMN_ROLE_SCOPE_DIMENSION = "scope_dimension"
COLUMN_ROLE_NOTE = "note"


_NUMERIC_SIGNAL_RE = re.compile(
    r"(?:[%$€£¥₹]|(?:^|[\s(])(?:USD|EUR|GBP|JPY|CHF|AUD|CAD|CNY|INR|SAR|AED|EGP|QAR|KWD|OMR|BHD|TRY|ZAR)(?:$|[\s):,.;]))",
    flags=re.IGNORECASE,
)
_NUMBER_LIKE_RE = re.compile(r"[+-]?\d[\d,]*(?:[.:]\d+)?")
_NUMBER_WITH_UNIT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:[a-zA-Z]{1,6}|%)\b")


def _normalize_cell(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _numeric_signal(value: str) -> bool:
    sample = _normalize_cell(value)
    if not sample:
        return False
    return bool(
        _NUMERIC_SIGNAL_RE.search(sample)
        or _NUMBER_LIKE_RE.search(sample)
        or _NUMBER_WITH_UNIT_RE.search(sample)
    )


@dataclass(frozen=True)
class ColumnRoleProfile:
    column_index: int
    column_key: str
    role: str
    confidence: float
    role_scores: dict[str, float]
    signals: dict[str, float | int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "column_index": int(self.column_index),
            "column_key": str(self.column_key or ""),
            "role": str(self.role or ""),
            "confidence": round(float(self.confidence), 3),
            "role_scores": {str(key): round(float(value), 3) for key, value in (self.role_scores or {}).items()},
            "signals": dict(self.signals or {}),
        }


def _compute_column_signals(
    *,
    values: Sequence[str],
    data_row_count: int,
) -> dict[str, float | int]:
    cleaned = [_normalize_cell(value) for value in values if _normalize_cell(value)]
    non_empty_count = len(cleaned)
    if non_empty_count <= 0:
        return {
            "non_empty_count": 0,
            "non_empty_ratio": 0.0,
            "unique_count": 0,
            "unique_ratio": 0.0,
            "dominant_value_ratio": 0.0,
            "numeric_ratio": 0.0,
            "avg_chars": 0.0,
            "long_text_ratio": 0.0,
            "short_text_ratio": 0.0,
        }

    lower = [value.lower() for value in cleaned]
    frequency: dict[str, int] = {}
    for token in lower:
        frequency[token] = int(frequency.get(token, 0)) + 1
    dominant = max(frequency.values()) if frequency else 0
    unique_count = len(frequency)
    non_empty_ratio = float(non_empty_count) / float(max(1, data_row_count))
    unique_ratio = float(unique_count) / float(non_empty_count)
    numeric_ratio = float(sum(1 for value in cleaned if _numeric_signal(value))) / float(non_empty_count)
    avg_chars = float(sum(len(value) for value in cleaned)) / float(non_empty_count)
    long_text_ratio = float(
        sum(1 for value in cleaned if len(value) >= 18 or len(value.split()) >= 4)
    ) / float(non_empty_count)
    short_text_ratio = float(
        sum(1 for value in cleaned if len(value) <= 10 and len(value.split()) <= 2)
    ) / float(non_empty_count)
    dominant_ratio = float(dominant) / float(non_empty_count)

    return {
        "non_empty_count": int(non_empty_count),
        "non_empty_ratio": round(non_empty_ratio, 4),
        "unique_count": int(unique_count),
        "unique_ratio": round(unique_ratio, 4),
        "dominant_value_ratio": round(dominant_ratio, 4),
        "numeric_ratio": round(numeric_ratio, 4),
        "avg_chars": round(avg_chars, 3),
        "long_text_ratio": round(long_text_ratio, 4),
        "short_text_ratio": round(short_text_ratio, 4),
    }


def infer_column_roles(
    *,
    row_values: Sequence[Sequence[str]],
    column_schema: Sequence[str],
    header_row_indices: set[int] | None = None,
    min_scope_columns: int = 3,
) -> list[ColumnRoleProfile]:
    rows = list(row_values or [])
    width = max(
        len(column_schema or []),
        max((len(row or []) for row in rows), default=0),
    )
    if width <= 0:
        return []

    header_set = set(header_row_indices or set())
    data_rows = [list(row) for idx, row in enumerate(rows) if idx not in header_set]
    if not data_rows:
        data_rows = [list(row) for row in rows]
    data_row_count = max(1, len(data_rows))

    labels: list[str] = []
    for idx in range(width):
        label = str(column_schema[idx] if idx < len(column_schema) else "").strip()
        labels.append(label or f"column_{idx + 1}")

    signals_by_index: dict[int, dict[str, float | int]] = {}
    for col_idx in range(width):
        values = []
        for row in data_rows:
            values.append(str(row[col_idx]) if col_idx < len(row) else "")
        signals_by_index[col_idx] = _compute_column_signals(values=values, data_row_count=data_row_count)

    descriptor_confidence: dict[int, float] = {}
    qualifier_confidence: dict[int, float] = {}
    note_confidence: dict[int, float] = {}

    for idx in range(width):
        signals = signals_by_index[idx]
        non_empty_ratio = float(signals.get("non_empty_ratio") or 0.0)
        unique_ratio = float(signals.get("unique_ratio") or 0.0)
        dominant_ratio = float(signals.get("dominant_value_ratio") or 0.0)
        numeric_ratio = float(signals.get("numeric_ratio") or 0.0)
        avg_chars = float(signals.get("avg_chars") or 0.0)
        long_ratio = float(signals.get("long_text_ratio") or 0.0)
        short_ratio = float(signals.get("short_text_ratio") or 0.0)

        descriptor_score = 0.0
        if unique_ratio >= 0.62:
            descriptor_score += 0.34
        if long_ratio >= 0.32 or avg_chars >= 16.0:
            descriptor_score += 0.28
        if numeric_ratio <= 0.42:
            descriptor_score += 0.18
        if non_empty_ratio >= 0.4:
            descriptor_score += 0.1
        if dominant_ratio <= 0.45:
            descriptor_score += 0.1
        descriptor_confidence[idx] = _clamp(descriptor_score)

        qualifier_score = 0.0
        if non_empty_ratio >= 0.7:
            qualifier_score += 0.34
        if unique_ratio <= 0.5:
            qualifier_score += 0.22
        if avg_chars <= 14.0:
            qualifier_score += 0.2
        if short_ratio >= 0.45:
            qualifier_score += 0.12
        if dominant_ratio >= 0.28:
            qualifier_score += 0.12
        qualifier_confidence[idx] = _clamp(qualifier_score)

        note_score = 0.0
        if non_empty_ratio <= 0.3:
            note_score += 0.4
        if avg_chars >= 24.0 or long_ratio >= 0.58:
            note_score += 0.34
        if unique_ratio >= 0.65:
            note_score += 0.16
        if numeric_ratio <= 0.25:
            note_score += 0.1
        note_confidence[idx] = _clamp(note_score)

    descriptor_indices: list[int] = []
    for idx in range(width):
        if descriptor_confidence.get(idx, 0.0) >= 0.58 and qualifier_confidence.get(idx, 0.0) < 0.72:
            descriptor_indices.append(idx)
        else:
            break
    if not descriptor_indices and descriptor_confidence.get(0, 0.0) >= 0.52:
        descriptor_indices.append(0)
        if width > 1 and descriptor_confidence.get(1, 0.0) >= 0.48:
            descriptor_indices.append(1)

    note_indices: set[int] = {
        idx
        for idx in range(width)
        if note_confidence.get(idx, 0.0) >= 0.62 and idx not in descriptor_indices
    }
    qualifier_seed: set[int] = {
        idx
        for idx in range(width)
        if idx not in descriptor_indices
        and idx not in note_indices
        and qualifier_confidence.get(idx, 0.0) >= 0.62
    }
    scope_candidates: list[int] = [
        idx for idx in range(width) if idx not in descriptor_indices and idx not in note_indices
    ]

    rows_with_scope_values = 0
    sparse_rows = 0
    participation: dict[int, int] = {idx: 0 for idx in scope_candidates}
    singleton_hits: dict[int, int] = {idx: 0 for idx in scope_candidates}
    for row in data_rows:
        active: list[int] = []
        for idx in scope_candidates:
            value = _normalize_cell(row[idx] if idx < len(row) else "")
            if value:
                active.append(idx)
                participation[idx] = int(participation.get(idx, 0)) + 1
        if active:
            rows_with_scope_values += 1
        if len(active) == 1:
            sparse_rows += 1
            singleton_hits[active[0]] = int(singleton_hits.get(active[0], 0)) + 1
    sparse_ratio = (
        float(sparse_rows) / float(rows_with_scope_values)
        if rows_with_scope_values > 0
        else 0.0
    )

    scope_confidence: dict[int, float] = {}
    for idx in range(width):
        if idx not in scope_candidates:
            scope_confidence[idx] = 0.0
            continue
        signals = signals_by_index[idx]
        non_empty_ratio = float(signals.get("non_empty_ratio") or 0.0)
        participation_count = int(participation.get(idx, 0))
        singleton_count = int(singleton_hits.get(idx, 0))
        singleton_ratio = float(singleton_count) / float(max(1, participation_count))
        participation_ratio = float(participation_count) / float(max(1, rows_with_scope_values))
        score = 0.0
        if 0.08 <= non_empty_ratio <= 0.95:
            score += 0.24
        if non_empty_ratio <= 0.78:
            score += 0.18
        if singleton_ratio >= 0.22:
            score += 0.28
        if participation_ratio >= 0.12:
            score += 0.12
        if sparse_ratio >= 0.32 and non_empty_ratio < 0.88:
            score += 0.1
        if idx in qualifier_seed:
            score -= 0.1
        if descriptor_confidence.get(idx, 0.0) >= 0.55:
            score -= 0.12
        scope_confidence[idx] = _clamp(score)

    roles: dict[int, str] = {}
    confidence: dict[int, float] = {}
    for idx in range(width):
        d_conf = descriptor_confidence.get(idx, 0.0)
        q_conf = qualifier_confidence.get(idx, 0.0)
        s_conf = scope_confidence.get(idx, 0.0)
        n_conf = note_confidence.get(idx, 0.0)

        if idx in descriptor_indices:
            roles[idx] = COLUMN_ROLE_DESCRIPTOR
            confidence[idx] = d_conf
            continue
        if idx in note_indices and n_conf >= max(s_conf + 0.08, q_conf + 0.08, 0.6):
            roles[idx] = COLUMN_ROLE_NOTE
            confidence[idx] = n_conf
            continue
        if q_conf >= max(s_conf + 0.08, 0.56):
            roles[idx] = COLUMN_ROLE_QUALIFIER
            confidence[idx] = q_conf
            continue
        roles[idx] = COLUMN_ROLE_SCOPE_DIMENSION
        confidence[idx] = s_conf

    scope_indices = [idx for idx, role in roles.items() if role == COLUMN_ROLE_SCOPE_DIMENSION]
    min_scope_target = max(1, int(min_scope_columns))
    promotable = [
        idx
        for idx in scope_candidates
        if roles.get(idx) != COLUMN_ROLE_NOTE and roles.get(idx) != COLUMN_ROLE_DESCRIPTOR
    ]
    if len(scope_indices) < min_scope_target and promotable:
        ranked = sorted(promotable, key=lambda idx: scope_confidence.get(idx, 0.0), reverse=True)
        for idx in ranked:
            if idx in scope_indices:
                continue
            roles[idx] = COLUMN_ROLE_SCOPE_DIMENSION
            confidence[idx] = max(confidence.get(idx, 0.0), scope_confidence.get(idx, 0.0))
            scope_indices.append(idx)
            if len(scope_indices) >= min(min_scope_target, len(promotable)):
                break

    profiles: list[ColumnRoleProfile] = []
    for idx in range(width):
        role = roles.get(idx, COLUMN_ROLE_SCOPE_DIMENSION)
        role_scores = {
            COLUMN_ROLE_DESCRIPTOR: round(float(descriptor_confidence.get(idx, 0.0)), 4),
            COLUMN_ROLE_QUALIFIER: round(float(qualifier_confidence.get(idx, 0.0)), 4),
            COLUMN_ROLE_SCOPE_DIMENSION: round(float(scope_confidence.get(idx, 0.0)), 4),
            COLUMN_ROLE_NOTE: round(float(note_confidence.get(idx, 0.0)), 4),
        }
        signals = dict(signals_by_index.get(idx, {}))
        signals["sparse_row_ratio"] = round(float(sparse_ratio), 4)
        profiles.append(
            ColumnRoleProfile(
                column_index=idx,
                column_key=labels[idx],
                role=role,
                confidence=round(float(_clamp(confidence.get(idx, 0.0))), 3),
                role_scores=role_scores,
                signals=signals,
            )
        )
    return profiles


def column_role_groups(
    profiles: Sequence[ColumnRoleProfile],
) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {
        COLUMN_ROLE_DESCRIPTOR: [],
        COLUMN_ROLE_QUALIFIER: [],
        COLUMN_ROLE_SCOPE_DIMENSION: [],
        COLUMN_ROLE_NOTE: [],
    }
    for profile in profiles:
        groups.setdefault(profile.role, []).append(int(profile.column_index))
    for key in list(groups.keys()):
        groups[key] = sorted(set(groups[key]))
    return groups


def column_role_payloads(profiles: Sequence[ColumnRoleProfile]) -> list[dict[str, Any]]:
    return [profile.as_dict() for profile in profiles]


def role_lookup_by_index(
    values: Sequence[Mapping[str, Any]] | Sequence[ColumnRoleProfile],
) -> dict[int, dict[str, Any]]:
    lookup: dict[int, dict[str, Any]] = {}
    for entry in values or []:
        if isinstance(entry, ColumnRoleProfile):
            payload = entry.as_dict()
        elif isinstance(entry, Mapping):
            payload = dict(entry)
        else:
            continue
        try:
            idx = int(payload.get("column_index"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        lookup[idx] = payload
    return lookup
