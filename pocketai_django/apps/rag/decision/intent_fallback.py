from __future__ import annotations

import json
import logging
import re
from typing import Callable, Mapping, Sequence

from apps.llm.ai_prompt_builder import PromptBundle
from apps.llm.llm_provider import BaseLLMProvider, PromptGenerationError, load_default_provider
from apps.rag.query.classifier import QueryClassification, QueryClassifier, QueryIntent

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", flags=re.DOTALL)


def _coerce_intent(value: object) -> QueryIntent | None:
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not raw:
        return None
    for intent in QueryIntent:
        if raw == intent.value:
            return intent
    return None


def _coerce_confidence(value: object, *, default: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return max(0.0, min(1.0, float(default)))
    return max(0.0, min(1.0, confidence))


def _coerce_list(value: object, *, max_items: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        values.append(token)
        if len(values) >= max_items:
            break
    return values


def _extract_json_mapping(text: str) -> Mapping[str, object] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    stripped = _JSON_FENCE_RE.sub("", raw).strip()
    candidates = [stripped]
    match = _JSON_OBJECT_RE.search(stripped)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return parsed
    return None


def _scope_for_intent(intent: QueryIntent) -> str:
    if intent in (QueryIntent.ENUMERATE, QueryIntent.AGGREGATE):
        return "all"
    if intent == QueryIntent.COMPARE:
        return "subset"
    if intent == QueryIntent.SPECIFIC_LOOKUP:
        return "specific"
    return "unknown"


class IntentFallbackService:
    """
    LLM fallback for low-confidence heuristic query classifications.

    Provider response contract:
    - provider returns outer JSON with `response_text` string.
    - `response_text` must contain a JSON object describing intent fields.
    """

    def __init__(
        self,
        *,
        provider_loader: Callable[[], BaseLLMProvider | None] = load_default_provider,
        max_terms: int = 40,
    ) -> None:
        self._provider_loader = provider_loader
        self.max_terms = max(5, int(max_terms))

    def classify(
        self,
        *,
        query: str,
        heuristic: QueryClassification,
        tenant_entity_terms: Sequence[str] = (),
        tenant_attribute_terms: Sequence[str] = (),
        table_columns: Sequence[str] = (),
        row_label_terms: Sequence[str] = (),
    ) -> QueryClassification | None:
        provider = self._provider_loader()
        if provider is None:
            return None

        prompt = self._build_prompt(
            query=query,
            heuristic=heuristic,
            tenant_entity_terms=tenant_entity_terms,
            tenant_attribute_terms=tenant_attribute_terms,
            table_columns=table_columns,
            row_label_terms=row_label_terms,
        )

        try:
            payload = provider.generate(prompt)
        except PromptGenerationError as exc:
            logger.warning("intent_fallback.provider_error error=%s", str(exc)[:240])
            return None
        except Exception as exc:  # pragma: no cover - safety net around external providers
            logger.warning("intent_fallback.unexpected_error error=%s", str(exc)[:240])
            return None

        parsed = self._extract_decision_payload(payload)
        if not parsed:
            return None

        intent = _coerce_intent(parsed.get("intent"))
        if intent is None:
            return None

        entity_type = str(parsed.get("entity_type") or "").strip() or heuristic.entity_type
        entity_names = _coerce_list(parsed.get("entity_names"), max_items=8) or list(heuristic.entity_names)
        attributes = _coerce_list(parsed.get("attributes"), max_items=12) or list(heuristic.attributes)
        confidence = _coerce_confidence(parsed.get("confidence"), default=heuristic.confidence)
        scope = str(parsed.get("scope") or "").strip().lower()
        if scope not in {"all", "specific", "subset", "unknown"}:
            scope = _scope_for_intent(intent)
        reasoning = str(parsed.get("reasoning") or "").strip() or "LLM fallback intent classification."

        hint_builder = QueryClassifier()
        retrieval_hints = hint_builder._generate_retrieval_hints(intent, entity_type, entity_names)
        return QueryClassification(
            intent=intent,
            entity_type=entity_type,
            entity_names=entity_names,
            attributes=attributes,
            scope=scope,
            confidence=confidence,
            reasoning=reasoning,
            retrieval_hints=retrieval_hints,
            source="llm_fallback",
            fallback_used=True,
        )

    def _extract_decision_payload(self, provider_payload: Mapping[str, object] | None) -> Mapping[str, object] | None:
        if isinstance(provider_payload, Mapping):
            if "intent" in provider_payload:
                return provider_payload
            response_text = provider_payload.get("response_text")
            if isinstance(response_text, str):
                parsed = _extract_json_mapping(response_text)
                if parsed:
                    return parsed
            content = provider_payload.get("content")
            if isinstance(content, str):
                parsed = _extract_json_mapping(content)
                if parsed:
                    return parsed
        return None

    def _build_prompt(
        self,
        *,
        query: str,
        heuristic: QueryClassification,
        tenant_entity_terms: Sequence[str],
        tenant_attribute_terms: Sequence[str],
        table_columns: Sequence[str],
        row_label_terms: Sequence[str],
    ) -> PromptBundle:
        def _sample(values: Sequence[str], *, limit: int) -> list[str]:
            sampled: list[str] = []
            seen: set[str] = set()
            for value in values:
                token = str(value or "").strip()
                if not token:
                    continue
                lowered = token.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                sampled.append(token)
                if len(sampled) >= limit:
                    break
            return sampled

        sampled_entities = _sample(tenant_entity_terms, limit=self.max_terms)
        sampled_attributes = _sample(tenant_attribute_terms, limit=self.max_terms)
        sampled_columns = _sample(table_columns, limit=self.max_terms)
        sampled_rows = _sample(row_label_terms, limit=self.max_terms)

        system_prompt = (
            "You classify search query intent for a multi-tenant RAG retrieval service. "
            "Always use tenant hints first when available. "
            "Return valid JSON using the outer schema with keys response_text/actions/extractions. "
            "Set actions and extractions to empty arrays. "
            "Set response_text to a compact JSON object string with keys: "
            "intent, confidence, entity_type, entity_names, attributes, scope, reasoning. "
            "intent must be one of enumerate,specific_lookup,compare,aggregate,exploratory."
        )
        user_prompt = (
            f"query: {query}\n"
            f"heuristic_intent: {heuristic.intent.value}\n"
            f"heuristic_confidence: {heuristic.confidence:.3f}\n"
            f"heuristic_entity_type: {heuristic.entity_type or ''}\n"
            f"heuristic_entity_names: {json.dumps(list(heuristic.entity_names), ensure_ascii=False)}\n"
            f"heuristic_attributes: {json.dumps(list(heuristic.attributes), ensure_ascii=False)}\n"
            f"tenant_entity_terms: {json.dumps(sampled_entities, ensure_ascii=False)}\n"
            f"tenant_attribute_terms: {json.dumps(sampled_attributes, ensure_ascii=False)}\n"
            f"table_columns: {json.dumps(sampled_columns, ensure_ascii=False)}\n"
            f"row_label_terms: {json.dumps(sampled_rows, ensure_ascii=False)}\n"
            "Choose the best intent for retrieval routing and keep confidence realistic."
        )
        return PromptBundle(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            transcript=tuple(),
            knowledge_snippets=tuple(),
            actions_catalog=tuple(),
        )
