"""
Memory Extraction Service for Agent Runs

Extracts structured facts and decisions from tool executions to persist
in MemoryItem for context preservation across turns.

Uses a hybrid approach:
- Rule-based extraction for known tool patterns (fast, deterministic)
- LLM-based extraction for complex/unknown tools (flexible, semantic)
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Mapping

from django.conf import settings
from django.utils import timezone

from apps.rag.rag_logging import structured_log

logger = logging.getLogger(__name__)


class MemoryExtractionService:
    """
    Extracts structured facts from tool executions for context preservation.

    This service analyzes tool results and extracts key information that should
    be persisted as MemoryItem records. This ensures that important
    data survives context compaction and approval flows.
    """

    OPERATIONAL_ID_FIELDS = {
        "id",
        "draft_id",
        "draftid",
        "message_id",
        "messageid",
        "thread_id",
        "threadid",
        "event_id",
        "eventid",
    }

    # Rule-based extraction patterns per tool type
    EXTRACTION_RULES: dict[str, dict[str, Any]] = {
        # Knowledge search tools - extract fees, prices, rates
        "search_knowledge": {
            "patterns": [
                # Currency amounts (EGP, USD, EUR, etc.)
                (r"(?:fee|price|cost|charge|amount)[:\s]*(?:EGP|USD|EUR|GBP|AED)?\s*(\d+(?:,\d{3})*(?:\.\d{2})?)", "amount"),
                (r"(?:EGP|USD|EUR|GBP|AED)\s*(\d+(?:,\d{3})*(?:\.\d{2})?)", "currency_amount"),
                # Percentages and rates
                (r"(?:rate|percentage|interest)[:\s]*(\d+(?:\.\d+)?)\s*%", "rate"),
                # Dates
                (r"(?:date|deadline|due)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "date"),
            ],
            "extract_fields": ["snippet", "title", "document_name"],
            "store_as": "extracted_data",
        },
        "read_knowledge": {
            "patterns": [
                (r"(?:fee|price|cost|charge|amount)[:\s]*(?:EGP|USD|EUR|GBP|AED)?\s*(\d+(?:,\d{3})*(?:\.\d{2})?)", "amount"),
                (r"(?:EGP|USD|EUR|GBP|AED)\s*(\d+(?:,\d{3})*(?:\.\d{2})?)", "currency_amount"),
                (r"(?:rate|percentage|interest)[:\s]*(\d+(?:\.\d+)?)\s*%", "rate"),
            ],
            "extract_fields": ["content", "title"],
            "store_as": "extracted_data",
        },
        # Email tools - extract user-meaningful fields, not provider/tool ids.
        "email_create_draft": {
            "extract_fields": ["recipient", "to", "subject"],
            "store_as": "decision",
            "key_prefix": "email_draft",
        },
        "email_send": {
            "extract_fields": ["status", "recipient", "to"],
            "store_as": "decision",
            "key_prefix": "email_sent",
        },
        "email_send_draft": {
            "extract_fields": ["status"],
            "store_as": "decision",
            "key_prefix": "email_sent",
        },
        # Calendar tools
        "calendar_create_event": {
            "extract_fields": ["title", "start_time", "startTime"],
            "store_as": "decision",
            "key_prefix": "calendar_event",
        },
        # Generic patterns for unknown tools
        "_default": {
            "extract_fields": ["status", "result"],
            "store_as": "extracted_data",
        },
    }

    def __init__(self, use_llm_fallback: bool = True):
        """
        Initialize the extraction service.

        Args:
            use_llm_fallback: Whether to use LLM for complex extractions
        """
        self.use_llm_fallback = use_llm_fallback

    def extract_from_tool_result(
        self,
        *,
        run: Any,  # AgentRun
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        user: Any | None = None,  # User
    ) -> list[Any]:  # list[MemoryItem]
        """
        Extract facts/decisions from a tool result and create memory items.

        Uses rule-based extraction first, falls back to LLM for complex cases.

        Args:
            run: The AgentRun this tool execution belongs to
            tool_name: Name of the tool that was executed
            arguments: Arguments passed to the tool
            result: Result returned by the tool
            user: Optional user who triggered the execution

        Returns:
            List of created MemoryItem records
        """
        from apps.conversations.models import MemoryItem, MemoryKind, MemoryScope, MemoryVisibility

        started = time.perf_counter()
        created_items: list[MemoryItem] = []
        llm_enabled = bool(getattr(settings, "MCP_MEMORY_LLM_EXTRACTION_ENABLED", False))
        llm_attempted = False
        llm_created = 0

        # Skip if tool failed
        status = str(result.get("status") or "").strip().lower()
        if status == "error":
            return created_items

        # Get extraction rules for this tool
        rules = self.EXTRACTION_RULES.get(tool_name, self.EXTRACTION_RULES.get("_default", {}))
        store_as = rules.get("store_as", "extracted_data")
        key_prefix = rules.get("key_prefix", tool_name)

        # Rule-based extraction
        extracted = self._rule_based_extraction(tool_name, arguments, result, rules)
        rule_based_created = 0

        # Create memory items from extracted data
        for key, value in extracted.items():
            if not value:
                continue
            if self._is_operational_id_field(key):
                continue

            # Determine the kind based on store_as
            kind = MemoryKind.EXTRACTED_DATA
            if store_as == "decision":
                kind = MemoryKind.DECISION
            elif store_as == "fact":
                kind = MemoryKind.FACT

            # Truncate content to max length
            content_str = str(value)[:4000]

            try:
                item = MemoryItem.objects.create(
                    business_profile=run.business_profile,
                    scope=MemoryScope.RUN,
                    agent_profile=run.agent_profile,
                    workflow=run.workflow,
                    run=run,
                    conversation=run.conversation,
                    kind=kind,
                    key=f"{key_prefix}_{key}",
                    content=content_str,
                    payload={
                        "tool_name": tool_name,
                        "extraction_method": "rule_based",
                        "extracted_at": timezone.now().isoformat(),
                        "original_key": key,
                    },
                    visibility=MemoryVisibility.PRIVATE,
                    created_by=user,
                )
                created_items.append(item)
                rule_based_created += 1
                if getattr(run, "workflow_id", None) and kind in {MemoryKind.DECISION, MemoryKind.EXTRACTED_DATA, MemoryKind.FACT}:
                    MemoryItem.objects.create(
                        business_profile=run.business_profile,
                        scope=MemoryScope.WORKFLOW,
                        agent_profile=run.agent_profile,
                        workflow=run.workflow,
                        run=run,
                        conversation=run.conversation,
                        kind=kind,
                        key=f"{key_prefix}_{key}",
                        content=content_str,
                        payload={
                            "tool_name": tool_name,
                            "extraction_method": "rule_based",
                            "extracted_at": timezone.now().isoformat(),
                            "original_key": key,
                            "source_run_id": str(run.id),
                        },
                        visibility=MemoryVisibility.SHARED,
                        created_by=user,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to create memory item: run=%s tool=%s key=%s error=%s",
                    run.id, tool_name, key, exc
                )

        # LLM fallback for complex extractions (if enabled and no extractions yet)
        if self.use_llm_fallback and not extracted:
            llm_attempted = True
            llm_extracted = self._llm_extraction(tool_name, arguments, result)
            for item_data in llm_extracted:
                try:
                    kind_str = item_data.get("kind", "extracted_data")
                    kind = getattr(MemoryKind, kind_str.upper(), MemoryKind.EXTRACTED_DATA)

                    item = MemoryItem.objects.create(
                        business_profile=run.business_profile,
                        scope=MemoryScope.RUN,
                        agent_profile=run.agent_profile,
                        workflow=run.workflow,
                        run=run,
                        conversation=run.conversation,
                        kind=kind,
                        key=item_data.get("key", f"{tool_name}_llm_extracted"),
                        content=str(item_data.get("content", ""))[:4000],
                        payload={
                            "tool_name": tool_name,
                            "extraction_method": "llm",
                            "extracted_at": timezone.now().isoformat(),
                            "confidence": item_data.get("confidence", 0.8),
                        },
                        visibility=MemoryVisibility.PRIVATE,
                        created_by=user,
                    )
                    created_items.append(item)
                    llm_created += 1
                    if getattr(run, "workflow_id", None):
                        MemoryItem.objects.create(
                            business_profile=run.business_profile,
                            scope=MemoryScope.WORKFLOW,
                            agent_profile=run.agent_profile,
                            workflow=run.workflow,
                            run=run,
                            conversation=run.conversation,
                            kind=kind,
                            key=item_data.get("key", f"{tool_name}_llm_extracted"),
                            content=str(item_data.get("content", ""))[:4000],
                            payload={
                                "tool_name": tool_name,
                                "extraction_method": "llm",
                                "extracted_at": timezone.now().isoformat(),
                                "confidence": item_data.get("confidence", 0.8),
                                "source_run_id": str(run.id),
                            },
                            visibility=MemoryVisibility.SHARED,
                            created_by=user,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to create LLM-extracted memory item: run=%s tool=%s error=%s",
                        run.id, tool_name, exc
                    )

        duration_ms = int((time.perf_counter() - started) * 1000.0)
        warn_ms = int(getattr(settings, "MCP_SLO_MEMORY_EXTRACTION_WARN_MS", 2500) or 0)
        slow = bool(warn_ms and duration_ms >= warn_ms)
        if created_items or llm_attempted or slow:
            kind_counts: dict[str, int] = {}
            for item in created_items:
                kind_key = str(getattr(item, "kind", "") or "").strip() or "unknown"
                kind_counts[kind_key] = kind_counts.get(kind_key, 0) + 1

            detail: dict[str, object] = {
                "tool_name": tool_name,
                "tool_status": status or "ok",
                "duration_ms": duration_ms,
                "created_items": len(created_items),
                "rule_based_created": rule_based_created,
                "llm_enabled": llm_enabled,
                "llm_attempted": llm_attempted,
                "llm_created": llm_created,
                "kinds": kind_counts,
            }
            if slow:
                detail["slo"] = "slow"
                detail["slo_warn_ms"] = warn_ms
            structured_log(
                "mcp",
                "memory.extraction",
                detail,
                context={
                    "business": getattr(run, "business_profile_id", None),
                    "run": getattr(run, "id", None),
                },
                logger_obj=logger,
                level=logging.WARNING if slow else logging.INFO,
            )

        return created_items

    def _is_operational_id_field(self, key: object) -> bool:
        normalized = str(key or "").strip().replace("-", "_").lower()
        compact = normalized.replace("_", "")
        return normalized in self.OPERATIONAL_ID_FIELDS or compact in self.OPERATIONAL_ID_FIELDS

    def _rule_based_extraction(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        rules: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Apply rule-based extraction patterns to tool result.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments
            result: Tool result
            rules: Extraction rules for this tool

        Returns:
            Dictionary of extracted key-value pairs
        """
        extracted: dict[str, Any] = {}

        # Extract specific fields from result
        extract_fields = rules.get("extract_fields", [])
        for field in extract_fields:
            value = self._deep_get(result, field)
            if value:
                extracted[field] = value

        # Apply regex patterns to text content
        patterns = rules.get("patterns", [])
        if patterns:
            # Collect all text content from result
            text_content = self._collect_text_content(result)

            for pattern, label in patterns:
                try:
                    matches = re.findall(pattern, text_content, re.IGNORECASE)
                    if matches:
                        # Store first match (or all if multiple)
                        if len(matches) == 1:
                            extracted[label] = matches[0]
                        else:
                            extracted[label] = matches[0]  # Take first, or could store all
                except re.error:
                    logger.warning("Invalid regex pattern: %s", pattern)

        # Also extract from arguments for context
        for arg_key in ["query", "search_query", "recipient", "to", "subject"]:
            if arg_key in arguments and arg_key not in extracted:
                extracted[f"arg_{arg_key}"] = arguments[arg_key]

        return extracted

    def _llm_extraction(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Use LLM to extract facts from complex tool results.

        This is a fallback when rule-based extraction doesn't find anything.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments
            result: Tool result

        Returns:
            List of extracted fact dictionaries with keys: kind, key, content, confidence
        """
        # Check if LLM extraction is enabled
        if not getattr(settings, "MCP_MEMORY_LLM_EXTRACTION_ENABLED", False):
            return []

        try:
            from apps.llm.llm_provider import load_default_provider

            provider = load_default_provider()
            if not provider:
                return []

            # Build extraction prompt
            prompt = self._build_extraction_prompt(tool_name, arguments, result)

            messages = [
                {"role": "system", "content": "You are a data extraction assistant. Extract key facts from tool execution results."},
                {"role": "user", "content": prompt},
            ]

            response = provider.chat(
                messages=messages,
                temperature=0.1,
                max_tokens=500,
            )

            # Parse response
            content = response.get("content", "")
            return self._parse_llm_extraction_response(content)

        except Exception as exc:
            logger.warning("LLM extraction failed for tool=%s: %s", tool_name, exc)
            return []

    def _build_extraction_prompt(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        """Build the prompt for LLM extraction."""
        # Truncate result to avoid token limits
        result_str = json.dumps(result, ensure_ascii=False)[:2000]
        args_str = json.dumps(arguments, ensure_ascii=False)[:500]

        return f"""Extract key facts from this tool execution:

Tool: {tool_name}
Arguments: {args_str}
Result: {result_str}

Extract:
1. Key data points (amounts, IDs, names, dates)
2. Important decisions or outcomes
3. Status or result indicators

Return as JSON array:
[{{"kind": "extracted_data|decision|fact", "key": "descriptive_key", "content": "value", "confidence": 0.0-1.0}}]

Only return facts that are clearly present. If nothing significant, return empty array []."""

    def _parse_llm_extraction_response(self, content: str) -> list[dict[str, Any]]:
        """Parse the LLM's extraction response."""
        try:
            # Try to find JSON array in response
            content = content.strip()

            # Handle markdown code blocks
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip()

            # Parse JSON
            if content.startswith("["):
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    return [
                        item for item in parsed
                        if isinstance(item, dict) and "key" in item and "content" in item
                    ]

            return []
        except (json.JSONDecodeError, ValueError):
            return []

    def _deep_get(self, obj: Any, key: str) -> Any:
        """Get a value from nested dict using dot notation or simple key."""
        if not isinstance(obj, Mapping):
            return None

        # Try direct key first
        if key in obj:
            return obj[key]

        # Try nested access with dots
        if "." in key:
            parts = key.split(".")
            current = obj
            for part in parts:
                if isinstance(current, Mapping) and part in current:
                    current = current[part]
                else:
                    return None
            return current

        return None

    def _collect_text_content(self, obj: Any, max_depth: int = 3) -> str:
        """Recursively collect text content from a nested structure."""
        if max_depth <= 0:
            return ""

        if isinstance(obj, str):
            return obj

        if isinstance(obj, Mapping):
            parts = []
            for key, value in obj.items():
                if key in ("snippet", "content", "text", "body", "message", "description"):
                    parts.append(self._collect_text_content(value, max_depth - 1))
            return " ".join(parts)

        if isinstance(obj, (list, tuple)):
            parts = []
            for item in obj[:10]:  # Limit list traversal
                parts.append(self._collect_text_content(item, max_depth - 1))
            return " ".join(parts)

        return str(obj) if obj else ""


def extract_workflow_state(
    *,
    run: Any,
    current_step: int,
    total_steps: int,
    step_description: str,
    completed_steps: list[str] | None = None,
    user: Any | None = None,
) -> Any | None:
    """
    Create a workflow state memory item to track multi-step execution progress.

    Args:
        run: The AgentRun
        current_step: Current step number (1-indexed)
        total_steps: Total number of steps
        step_description: Description of current step
        completed_steps: List of completed step descriptions
        user: Optional user

    Returns:
        Created MemoryItem or None
    """
    from apps.conversations.models import MemoryItem, MemoryKind, MemoryScope, MemoryVisibility

    try:
        content = f"Step {current_step}/{total_steps}: {step_description}"

        payload = {
            "current_step": current_step,
            "total_steps": total_steps,
            "step_description": step_description,
            "completed_steps": completed_steps or [],
            "created_at": timezone.now().isoformat(),
        }

        return MemoryItem.objects.create(
            business_profile=run.business_profile,
            scope=MemoryScope.RUN,
            agent_profile=run.agent_profile,
            workflow=run.workflow,
            run=run,
            conversation=run.conversation,
            kind=MemoryKind.STATE_NOTE,
            key=f"workflow_step_{current_step}",
            content=content,
            payload=payload,
            visibility=MemoryVisibility.PRIVATE,
            created_by=user,
        )
    except Exception as exc:
        logger.warning("Failed to create workflow state: run=%s error=%s", run.id, exc)
        return None
