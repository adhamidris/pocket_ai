from __future__ import annotations

from typing import Mapping, Sequence

from apps.rag.contracts import KnowledgeSnippet, QueryTraits
from apps.rag.decision.auto_arbitration import AutoArbitrationMixin
from apps.rag.decision.conflict_detection import ConflictDetectionMixin
from apps.rag.decision.decision_contract import AutoDecisionContractMixin


SCOPE_CATEGORY_MAX_DEFAULT = 40
SCOPE_TOP_CATEGORY_MAX_DEFAULT = 4


class SearchAutoDecisionMixin(
    AutoDecisionContractMixin,
    AutoArbitrationMixin,
    ConflictDetectionMixin,
):

    @staticmethod
    def _derive_no_result_reason(
        *,
        diagnostics: Mapping[str, object],
        table_blocked: bool,
    ) -> str:
        if table_blocked or str(diagnostics.get("table_reason") or "").strip().lower() == "specific_tokens_missing":
            return "not_applicable_to_segment"

        def _safe_int(value: object) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        candidate_signals = (
            _safe_int(diagnostics.get("chunk_candidate_count")),
            _safe_int(diagnostics.get("chunk_candidate_count_raw")),
            _safe_int(diagnostics.get("vector_candidates")),
            _safe_int(diagnostics.get("vector_candidates_post_threshold")),
            _safe_int(diagnostics.get("fts_candidates")),
            _safe_int(diagnostics.get("alias_hits")),
        )
        if any(value > 0 for value in candidate_signals):
            return "insufficient_evidence"

        path = str(diagnostics.get("path") or "").strip().lower()
        if path in {"hybrid", "parallel_rrf", "table_direct", "table_blended"}:
            return "insufficient_evidence"
        return "not_found"

    def _apply_phase6_semantics(
        self,
        *,
        status: str,
        snippets: Sequence[KnowledgeSnippet],
        diagnostics: Mapping[str, object],
        business_profile=None,
        traits: QueryTraits,
        table_context: Mapping[str, object] | None,
        table_blocked: bool,
    ) -> tuple[str, tuple[KnowledgeSnippet, ...], dict[str, object]]:
        updated_status = str(status or "not_found").strip().lower() or "not_found"
        updated_snippets = tuple(snippets or ())
        updated_diagnostics: dict[str, object] = dict(diagnostics or {})
        updated_diagnostics.setdefault("conflict_detected", False)
        updated_diagnostics.setdefault("no_result_reason", None)

        conflict_payload: dict[str, object] | None = None
        if (
            updated_status == "ok"
            and updated_snippets
            and not traits.is_identifier_like
        ):
            detected_conflict = self._detect_conflicting_evidence(
                snippets=updated_snippets,
                business_profile=business_profile,
                traits=traits,
                table_context=table_context,
            )
            if detected_conflict:
                # Conflicts should be non-blocking in agentic RAG. Keep evidence, mark the conflict,
                # and let the assistant explain uncertainty or present both values if needed.
                conflict_payload = dict(detected_conflict)
                updated_diagnostics["conflict_detected"] = True
                updated_diagnostics["conflict_context"] = conflict_payload
                updated_diagnostics["reason"] = "conflicting_evidence"

        no_result_reason = None
        if updated_status == "not_found":
            no_result_reason = self._derive_no_result_reason(
                diagnostics=updated_diagnostics,
                table_blocked=table_blocked,
            )
            updated_diagnostics["no_result_reason"] = no_result_reason

        requires_clarification = bool(updated_status == "needs_clarification")
        scope_summary = (
            updated_diagnostics.get("scope_summary")
            if isinstance(updated_diagnostics.get("scope_summary"), Mapping)
            else None
        )
        categories: tuple[str, ...] = tuple()
        top_categories: tuple[str, ...] = tuple()
        if isinstance(scope_summary, Mapping):
            categories, top_categories = self._scope_categories_for_contract(
                scope_summary=scope_summary,
            )
        if categories and "categories" not in updated_diagnostics:
            updated_diagnostics["categories"] = list(categories)
        if top_categories and "top_categories" not in updated_diagnostics:
            updated_diagnostics["top_categories"] = list(top_categories)
        current_ui_mode = str(updated_diagnostics.get("clarification_ui_mode") or "").strip().lower()
        if requires_clarification and current_ui_mode not in {"text"}:
            updated_diagnostics["clarification_ui_mode"] = "text"
        existing_contract = updated_diagnostics.get("auto_decision_contract")
        if isinstance(existing_contract, Mapping):
            contract = dict(existing_contract)
            if requires_clarification:
                contract["decision"] = "clarification"
            contract["needs_clarification"] = requires_clarification
            contract["scope_summary"] = dict(scope_summary) if isinstance(scope_summary, Mapping) else contract.get("scope_summary")
            contract["categories"] = list(updated_diagnostics.get("categories") or contract.get("categories") or [])
            contract["top_categories"] = list(updated_diagnostics.get("top_categories") or contract.get("top_categories") or [])
            contract_ui_mode = str(updated_diagnostics.get("clarification_ui_mode") or contract.get("clarification_ui_mode") or "").strip().lower()
            contract["clarification_ui_mode"] = contract_ui_mode if contract_ui_mode in {"text"} else None
            contract["conflict_detected"] = bool(updated_diagnostics.get("conflict_detected"))
            contract["no_result_reason"] = (
                str(updated_diagnostics.get("no_result_reason")).strip().lower()
                if str(updated_diagnostics.get("no_result_reason") or "").strip()
                else None
            )
            updated_diagnostics["auto_decision_contract"] = contract
        else:
            updated_diagnostics["auto_decision_contract"] = self._derive_auto_decision_contract(
                route_diagnostics=updated_diagnostics,
                scoring_diagnostics=updated_diagnostics,
                requires_clarification=requires_clarification,
                scope_summary=scope_summary,
                conflict_detected=bool(updated_diagnostics.get("conflict_detected")),
                no_result_reason=str(updated_diagnostics.get("no_result_reason") or "") or None,
            )
        return updated_status, updated_snippets, updated_diagnostics
