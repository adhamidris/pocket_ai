from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from apps.accounts.models import (
    KnowledgeUpload,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    _normalize_identifier_token,
)


@dataclass
class ColumnSample:
    column_name: str
    normalized_name: str
    sheet_name: str
    values: list[str]


class ValueAwareIdentifierDetector:
    """
    Lightweight value-based detector that combines header hints with sampled column values.

    Intent: detect likely identifier columns without relying on brittle aliases.
    Feeds IdentifierGuardrail so MCP can gate reads/searches on required identifiers.
    """

    HEADER_ALIASES = {
        "email": {"email", "e-mail", "mail", "primary_email", "work_email", "contact_email", "applicant_email"},
        "phone": {"phone", "mobile", "cell", "contact_number", "phone_number", "tel", "mobile_number"},
        "customer_id": {"customer_id", "customerid", "cust_id", "custid", "account_id", "accountid", "user_id", "userid", "id", "applicant_id"},
        "external_id": {"external_id", "externalid", "reference", "ref_id", "refid", "ticket_id", "case_id", "order_id", "incident_id"},
    }

    DISPLAY_NAMES = {
        "email": "Email",
        "phone": "Phone",
        "customer_id": "Customer ID",
        "external_id": "External ID",
    }

    EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
    PHONE_RE = re.compile(r"^\+?[\d\-\s().]{6,}$")
    PREFIX_ID_RE = re.compile(r"(?i)^(?:tck|tkt|cas|case|ord|order|inc|incident|ref|req)[-_]?[a-z0-9]+$")
    MIXED_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{4,}$", re.IGNORECASE)

    def __init__(self, *, row_sample_limit: int = 50, total_value_cap: int = 600, model_assist: bool = False) -> None:
        self.row_sample_limit = row_sample_limit
        self.total_value_cap = total_value_cap
        self.model_assist = model_assist

    def detect(self, *, upload: KnowledgeUpload | None, headers: Sequence[str] | None = None) -> list[dict[str, object]]:
        """Suggest identifier column mappings using header + value signals."""
        header_matches = self._header_candidates(headers or [], model_assist=self.model_assist)
        value_samples = self._collect_column_samples(upload) if upload else []
        value_candidates = self._value_candidates(value_samples, model_assist=self.model_assist)

        proposals: list[dict[str, object]] = []
        seen_keys: set[str] = set()

        # Prefer columns we have values for; merge header + value evidence.
        for sample in value_samples:
            header_candidate = header_matches.get(sample.normalized_name)
            value_candidate = value_candidates.get(sample.normalized_name)
            candidate = self._combine_candidates(
                header_candidate=header_candidate,
                value_candidate=value_candidate,
                fallback_name=sample.column_name,
                sheet_name=sample.sheet_name,
            )
            if not candidate:
                continue
            key = candidate["key"]
            if key in seen_keys:
                # Keep the first high-confidence mapping per identifier to avoid noise.
                continue
            seen_keys.add(key)
            proposals.append(candidate)

        # Add header-only matches for columns we did not see values for.
        for normalized, header_candidate in header_matches.items():
            key = header_candidate["key"]
            if key in seen_keys:
                continue
            proposals.append(
                {
                    "key": key,
                    "display_name": self.DISPLAY_NAMES.get(key, key.title()),
                    "column_name": header_candidate["column_name"],
                    "sheet_name": "",
                    "confidence": header_candidate["confidence"],
                    "source": "header",
                    "diagnostics": {"match_type": "header_only", "normalized_column": normalized},
                }
            )
            seen_keys.add(key)

        # Cap to a small set to avoid overloading the UI/API.
        proposals.sort(key=lambda item: item.get("confidence") or 0, reverse=True)
        return proposals[:6]

    def _combine_candidates(
        self,
        *,
        header_candidate: dict[str, object] | None,
        value_candidate: dict[str, object] | None,
        fallback_name: str,
        sheet_name: str,
    ) -> dict[str, object] | None:
        """Merge header/value candidates, preferring value evidence when keys agree."""
        if not header_candidate and not value_candidate:
            return None

        # If only one signal is present, use it as-is.
        if value_candidate and not header_candidate:
            return value_candidate
        if header_candidate and not value_candidate:
            return {
                "key": header_candidate["key"],
                "display_name": self.DISPLAY_NAMES.get(header_candidate["key"], header_candidate["key"].title()),
                "column_name": header_candidate["column_name"],
                "sheet_name": sheet_name,
                "confidence": header_candidate["confidence"],
                "source": "header",
                "diagnostics": {"match_type": "header_only", "normalized_column": header_candidate.get("normalized")},
            }

        assert header_candidate and value_candidate
        combined_key = value_candidate["key"]
        combined_confidence = value_candidate["confidence"]
        source = "values"
        diagnostics = {
            "header_key": header_candidate["key"],
            "value_key": value_candidate["key"],
            "header_confidence": header_candidate["confidence"],
            "value_confidence": value_candidate["confidence"],
        }

        if header_candidate["key"] == value_candidate["key"]:
            # Blend header + value signals, biasing towards values.
            combined_confidence = min(
                1.0,
                (value_candidate["confidence"] * 0.7) + (header_candidate["confidence"] * 0.3),
            )
            source = "both"
        else:
            diagnostics["header_column_name"] = header_candidate["column_name"]

        return {
            "key": combined_key,
            "display_name": self.DISPLAY_NAMES.get(combined_key, combined_key.title()),
            "column_name": value_candidate.get("column_name") or header_candidate["column_name"] or fallback_name,
            "sheet_name": sheet_name,
            "confidence": combined_confidence,
            "source": source,
            "diagnostics": {**value_candidate.get("diagnostics", {}), **diagnostics},
        }

    def _header_candidates(self, headers: Sequence[str], model_assist: bool = False) -> dict[str, dict[str, object]]:
        candidates: dict[str, dict[str, object]] = {}
        for raw in headers:
            header = (raw or "").strip()
            if not header:
                continue
            normalized = _normalize_identifier_token(header)
            if not normalized:
                continue
            match = self._match_header_key(normalized, header)
            semantic_diag = {}
            if not match and model_assist:
                semantic = self._semantic_match(header)
                if semantic:
                    match = (semantic["key"], semantic["score"])
                    semantic_diag = {"semantic_score": semantic["score"], "semantic_match": True}
            if not match:
                continue
            key, confidence = match
            candidates[normalized] = {
                "key": key,
                "column_name": header,
                "normalized": normalized,
                "confidence": confidence,
                "source": "header",
                "diagnostics": semantic_diag,
            }
        return candidates

    def _match_header_key(self, normalized: str, raw: str) -> tuple[str, float] | None:
        for key, aliases in self.HEADER_ALIASES.items():
            if normalized in aliases or raw.lower() in aliases:
                return key, 0.9

        lowered = raw.lower()
        if "email" in lowered or re.search(r"mail", lowered):
            return "email", 0.7
        if "phone" in lowered or re.search(r"(cell|mobile|tel)", lowered):
            return "phone", 0.7
        if re.search(r"(customer|account|user).*id", lowered):
            return "customer_id", 0.65
        if re.search(r"(ticket|case|order|incident).*id", lowered):
            return "external_id", 0.7
        if lowered.endswith("id") or lowered == "id":
            return "customer_id", 0.6
        return None

    def _semantic_match(self, header: str) -> dict[str, object] | None:
        """
        Lightweight semantic similarity for headers to known identifier types.
        Avoids external models; uses token overlap / sequence matching.
        """

        from difflib import SequenceMatcher

        candidates = {
            "email": ["email address", "contact email", "mail address"],
            "phone": ["phone number", "mobile number", "contact phone", "telephone"],
            "customer_id": ["customer identifier", "account number", "member id", "user number"],
            "external_id": ["reference id", "ticket number", "case reference", "order reference"],
        }
        header_tokens = header.lower().replace("_", " ").replace("-", " ")
        best_key = None
        best_score = 0.0
        for key, phrases in candidates.items():
            for phrase in phrases:
                score = SequenceMatcher(None, header_tokens, phrase).ratio()
                if score > best_score:
                    best_score = score
                    best_key = key
        if best_score >= 0.7 and best_key:
            return {"key": best_key, "score": round(best_score, 3)}
        return None

    def _collect_column_samples(self, upload: KnowledgeUpload) -> list[ColumnSample]:
        """
        Gather column samples with a global cap to avoid heavy scans on wide/long tables.
        """

        samples: dict[str, ColumnSample] = {}
        tables = KnowledgeUploadTable.objects.filter(upload=upload).order_by("order_index")[:4]
        total_values = 0
        for table in tables:
            header_map = self._extract_headers(table.column_schema)
            rows = (
                KnowledgeUploadTableRow.objects.filter(table=table)
                .prefetch_related("cells")
                .order_by("row_index")[: self.row_sample_limit]
            )
            for row in rows:
                if total_values >= self.total_value_cap:
                    break
                for cell in row.cells.all():
                    raw_value = str(cell.raw_text or "").strip()
                    if not raw_value:
                        continue
                    if len(raw_value) > 256:
                        raw_value = raw_value[:256]
                    if not raw_value:
                        continue
                    column_name = header_map.get(cell.column_index) or cell.column_key or f"column_{cell.column_index + 1}"
                    normalized = _normalize_identifier_token(column_name) or f"column_{cell.column_index + 1}"
                    sample_key = f"{table.id}:{normalized}"
                    sheet_name = table.section_heading or table.title or ""
                    sample = samples.get(sample_key)
                    if not sample:
                        sample = ColumnSample(
                            column_name=column_name,
                            normalized_name=normalized,
                            sheet_name=sheet_name,
                            values=[],
                        )
                        samples[sample_key] = sample
                    if len(sample.values) < self.row_sample_limit and total_values < self.total_value_cap:
                        sample.values.append(raw_value)
                        total_values += 1
                if total_values >= self.total_value_cap:
                    break
        return list(samples.values())

    def _extract_headers(self, column_schema: object) -> dict[int, str]:
        headers: dict[int, str] = {}
        if not isinstance(column_schema, list):
            return headers
        for idx, entry in enumerate(column_schema):
            if isinstance(entry, str) and entry.strip():
                headers[idx] = entry.strip()
            elif isinstance(entry, Mapping):
                label = entry.get("label") or entry.get("name")
                if isinstance(label, str) and label.strip():
                    headers[idx] = label.strip()
        return headers

    def _value_candidates(self, samples: Iterable[ColumnSample], model_assist: bool = False) -> dict[str, dict[str, object]]:
        candidates: dict[str, dict[str, object]] = {}
        for sample in samples:
            values = [value for value in sample.values if value]
            if not values:
                continue
            total = len(values)
            unique_ratio = self._unique_ratio(values)
            email_hits = sum(1 for value in values if self.EMAIL_RE.match(value))
            phone_hits = sum(1 for value in values if self._looks_like_phone(value))
            prefix_hits = sum(1 for value in values if self.PREFIX_ID_RE.match(value))
            mixed_id_hits = sum(1 for value in values if self._looks_like_mixed_id(value))
            numeric_id_hits = sum(1 for value in values if self._looks_like_numeric_id(value))
            email_ratio = email_hits / total
            phone_ratio = phone_hits / total
            prefix_ratio = prefix_hits / total
            mixed_ratio = mixed_id_hits / total
            numeric_ratio = numeric_id_hits / total

            best_key: str | None = None
            best_confidence = 0.0
            diagnostics = {
                "sample_size": total,
                "unique_ratio": round(unique_ratio, 3),
                "email_ratio": round(email_ratio, 3),
                "phone_ratio": round(phone_ratio, 3),
                "prefix_ratio": round(prefix_ratio, 3),
                "mixed_id_ratio": round(mixed_ratio, 3),
                "numeric_id_ratio": round(numeric_ratio, 3),
            }

            email_conf = self._confidence_from_ratio(email_ratio, unique_ratio)
            if email_conf >= 0.5 and email_conf > best_confidence:
                best_key = "email"
                best_confidence = email_conf

            phone_conf = self._confidence_from_ratio(phone_ratio, unique_ratio)
            if phone_conf >= 0.5 and phone_conf > best_confidence:
                best_key = "phone"
                best_confidence = phone_conf

            external_conf = self._confidence_from_ratio(max(prefix_ratio, mixed_ratio), unique_ratio)
            if external_conf >= 0.45 and external_conf > best_confidence:
                best_key = "external_id"
                best_confidence = external_conf

            customer_conf = self._confidence_from_ratio(numeric_ratio, unique_ratio)
            if customer_conf >= 0.45 and customer_conf > best_confidence:
                best_key = "customer_id"
                best_confidence = customer_conf

            if not best_key and model_assist:
                semantic = self._semantic_value_hint(values)
                if semantic:
                    best_key = semantic["key"]
                    best_confidence = max(best_confidence, semantic.get("score", 0.0))
                    diagnostics["semantic_match"] = True
                    diagnostics["semantic_score"] = semantic.get("score")

            if not best_key:
                continue

            candidates[sample.normalized_name] = {
                "key": best_key,
                "display_name": self.DISPLAY_NAMES.get(best_key, best_key.title()),
                "column_name": sample.column_name,
                "sheet_name": sample.sheet_name,
                "confidence": min(1.0, round(best_confidence, 3)),
                "source": "values",
                "diagnostics": diagnostics,
            }
        return candidates

    def _semantic_value_hint(self, values: Sequence[str]) -> dict[str, object] | None:
        """
        Very light semantic hint on sample values; aims to catch uncommon identifiers.
        Uses string similarity to known phrases to avoid external models.
        """

        from difflib import SequenceMatcher

        phrases = {
            "external_id": ["ticket", "case", "order", "reference", "incident"],
            "customer_id": ["customer", "account", "member", "user"],
        }
        sample = " ".join(values[:5]).lower()
        best_key = None
        best_score = 0.0
        for key, word_list in phrases.items():
            for word in word_list:
                score = SequenceMatcher(None, sample, word).ratio()
                if score > best_score:
                    best_score = score
                    best_key = key
        if best_key and best_score >= 0.6:
            return {"key": best_key, "score": round(best_score, 3)}
        return None

    def _confidence_from_ratio(self, ratio: float, unique_ratio: float) -> float:
        if ratio <= 0:
            return 0.0
        # Blend hit ratio with uniqueness; cap aggressively to avoid overconfidence on tiny samples.
        base = ratio * 0.7 + unique_ratio * 0.25
        bonus = 0.05 if ratio > 0.6 else 0.0
        return min(1.0, base + bonus)

    def _unique_ratio(self, values: Sequence[str]) -> float:
        if not values:
            return 0.0
        unique = len(set(values))
        return unique / len(values)

    def _looks_like_phone(self, value: str) -> bool:
        if not value:
            return False
        if self.EMAIL_RE.match(value):
            return False
        digits = re.sub(r"[^\d]", "", value)
        if len(digits) < 6 or len(digits) > 16:
            return False
        return bool(self.PHONE_RE.match(value))

    def _looks_like_mixed_id(self, value: str) -> bool:
        if not value:
            return False
        if self.EMAIL_RE.match(value) or self._looks_like_phone(value):
            return False
        return bool(self.MIXED_ID_RE.match(value))

    def _looks_like_numeric_id(self, value: str) -> bool:
        if not value:
            return False
        if self.EMAIL_RE.match(value) or self._looks_like_phone(value):
            return False
        digits = re.sub(r"\D", "", value)
        if not digits:
            return False
        return 4 <= len(digits) <= 16
