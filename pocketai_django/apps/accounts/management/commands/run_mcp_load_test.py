from __future__ import annotations

import json
import math
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.accounts.models import AgentProfile, BusinessProfile
from apps.conversations.models import Conversation, ConversationChannel, ConversationStatus
from apps.services.mcp import tools as mcp_tools
from apps.services.mcp.types import ToolExecutionContext


@dataclass(frozen=True)
class LoadResult:
    duration_ms: int
    status: str
    tool: str
    error_code: str | None


def _percentile(values: Sequence[int], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return float(sorted_vals[f])
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return float(d0 + d1)


class Command(BaseCommand):
    help = "Run a basic concurrent load test against MCP tools (search_knowledge/read_knowledge)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--business-id", type=str, required=True, help="BusinessProfile UUID.")
        parser.add_argument(
            "--mode",
            type=str,
            default="search",
            choices=("search", "search_read", "read_knowledge"),
            help="Which tool path to test.",
        )
        parser.add_argument("--queries", nargs="*", default=["pricing"], help="Queries to rotate through.")
        parser.add_argument("--iterations", type=int, default=200, help="Total calls to execute.")
        parser.add_argument("--concurrency", type=int, default=8, help="Number of threads.")
        parser.add_argument("--document-id", type=str, default="", help="Required for read_knowledge mode.")
        parser.add_argument("--intent", type=str, default="auto", help="read_knowledge intent (auto|text|table).")
        parser.add_argument("--output", type=str, default="", help="Optional JSON output path.")
        parser.add_argument(
            "--enforce",
            action="store_true",
            help="Fail (non-zero exit) when thresholds are violated.",
        )
        parser.add_argument(
            "--p95-max-ms",
            type=int,
            default=0,
            help="Maximum allowed p95 latency in ms (0 = use settings default).",
        )
        parser.add_argument(
            "--max-error-rate",
            type=float,
            default=-1.0,
            help="Maximum allowed error rate, as fraction 0..1 (-1 = use settings default).",
        )
        parser.add_argument(
            "--max-throttled-rate",
            type=float,
            default=-1.0,
            help="Maximum allowed throttled rate, as fraction 0..1 (-1 = use settings default).",
        )

    def handle(self, *args, **options):
        started_at = datetime.now(tz=timezone.utc)
        business_id_raw = str(options.get("business_id") or "").strip()
        try:
            business_id = uuid.UUID(business_id_raw)
        except (TypeError, ValueError):
            raise CommandError("Invalid --business-id") from None
        business = BusinessProfile.objects.filter(id=business_id).first()
        if not business:
            raise CommandError("BusinessProfile not found.")

        agent = AgentProfile.objects.filter(business_profile=business).order_by("created_at").first()
        conversation = Conversation.objects.create(
            business_profile=business,
            agent_profile=agent,
            channel=ConversationChannel.API,
            status=ConversationStatus.NEW,
        )

        mode = str(options.get("mode") or "search").strip()
        queries = [str(q).strip() for q in (options.get("queries") or []) if str(q).strip()]
        if not queries:
            queries = ["pricing"]
        iterations = int(options.get("iterations") or 0)
        iterations = 1 if iterations <= 0 else iterations
        concurrency = int(options.get("concurrency") or 0)
        concurrency = 1 if concurrency <= 0 else min(128, concurrency)

        document_id_raw = str(options.get("document_id") or "").strip()
        intent = str(options.get("intent") or "auto").strip().lower() or "auto"
        if mode == "read_knowledge":
            if not document_id_raw:
                raise CommandError("--document-id is required for mode=read_knowledge")
            try:
                uuid.UUID(document_id_raw)
            except (TypeError, ValueError):
                raise CommandError("Invalid --document-id UUID") from None

        def _run_one(index: int) -> LoadResult:
            close_old_connections()
            query_text = queries[index % len(queries)]
            started = time.perf_counter()
            tool_name = "search_knowledge"
            result: Mapping[str, Any]

            try:
                if mode == "search":
                    tool_name = "search_knowledge"
                    result = mcp_tools.execute_tool(
                        "search_knowledge",
                        {"query": query_text, "limit": 5},
                        conversation=conversation,
                        context=ToolExecutionContext(),
                    )
                elif mode == "search_read":
                    tool_name = "search_knowledge"
                    search_result = mcp_tools.execute_tool(
                        "search_knowledge",
                        {"query": query_text, "limit": 3},
                        conversation=conversation,
                        context=ToolExecutionContext(),
                    )
                    snippets = search_result.get("snippets") if isinstance(search_result.get("snippets"), list) else []
                    doc_id = ""
                    if snippets:
                        first = snippets[0] if isinstance(snippets[0], Mapping) else {}
                        doc_id = str(first.get("document_id") or first.get("upload_id") or "").strip()
                    if not doc_id:
                        result = search_result
                    else:
                        tool_name = "read_knowledge"
                        result = mcp_tools.execute_tool(
                            "read_knowledge",
                            {"document_id": doc_id, "intent": intent},
                            conversation=conversation,
                            context=ToolExecutionContext(),
                        )
                else:
                    tool_name = "read_knowledge"
                    result = mcp_tools.execute_tool(
                        "read_knowledge",
                        {"document_id": document_id_raw, "intent": intent},
                        conversation=conversation,
                        context=ToolExecutionContext(),
                    )
            finally:
                duration_ms = int((time.perf_counter() - started) * 1000.0)
                close_old_connections()

            status = str(result.get("status") or "").strip() or "unknown"
            error_code = str(result.get("error_code") or "").strip() or None
            return LoadResult(duration_ms=duration_ms, status=status, tool=tool_name, error_code=error_code)

        results: list[LoadResult] = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_run_one, idx) for idx in range(iterations)]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception:
                    results.append(LoadResult(duration_ms=0, status="error", tool="unknown", error_code="exception"))

        ended_at = datetime.now(tz=timezone.utc)
        durations = [r.duration_ms for r in results if r.duration_ms > 0]
        statuses: dict[str, int] = {}
        error_codes: dict[str, int] = {}
        tool_counts: dict[str, int] = {}
        for r in results:
            statuses[r.status] = statuses.get(r.status, 0) + 1
            tool_counts[r.tool] = tool_counts.get(r.tool, 0) + 1
            if r.error_code:
                error_codes[r.error_code] = error_codes.get(r.error_code, 0) + 1

        p50 = _percentile(durations, 50) if durations else 0.0
        p95 = _percentile(durations, 95) if durations else 0.0
        mean = float(statistics.mean(durations)) if durations else 0.0
        max_latency = max(durations) if durations else 0

        error_count = int(statuses.get("error", 0) + statuses.get("unknown", 0))
        throttled_count = int(statuses.get("throttled", 0))
        error_rate = float(error_count) / float(iterations) if iterations else 1.0
        throttled_rate = float(throttled_count) / float(iterations) if iterations else 1.0

        max_p95_ms = int(options.get("p95_max_ms") or 0)
        if max_p95_ms <= 0:
            max_p95_ms = int(getattr(settings, "MCP_LOAD_TEST_P95_MAX_MS", 1500) or 1500)
        max_error_rate = float(options.get("max_error_rate") if options.get("max_error_rate") is not None else -1.0)
        if max_error_rate < 0:
            max_error_rate = float(getattr(settings, "MCP_LOAD_TEST_MAX_ERROR_RATE", 0.02) or 0.02)
        max_throttled_rate = float(
            options.get("max_throttled_rate") if options.get("max_throttled_rate") is not None else -1.0
        )
        if max_throttled_rate < 0:
            max_throttled_rate = float(getattr(settings, "MCP_LOAD_TEST_MAX_THROTTLED_RATE", 0.02) or 0.02)

        violations: dict[str, Mapping[str, object]] = {}
        if durations:
            if float(p95) > float(max_p95_ms):
                violations["p95_latency_ms"] = {"observed": float(p95), "maximum": float(max_p95_ms)}
        else:
            violations["p95_latency_ms"] = {"observed": None, "maximum": float(max_p95_ms)}

        if error_rate > float(max_error_rate):
            violations["error_rate"] = {"observed": error_rate, "maximum": float(max_error_rate)}
        if throttled_rate > float(max_throttled_rate):
            violations["throttled_rate"] = {"observed": throttled_rate, "maximum": float(max_throttled_rate)}

        report: dict[str, object] = {
            "type": "mcp_load_test",
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "business_id": str(business.id),
            "conversation_id": str(conversation.id),
            "mode": mode,
            "queries": queries,
            "iterations": iterations,
            "concurrency": concurrency,
            "document_id": document_id_raw or None,
            "intent": intent,
            "rate_limits_disabled": bool(getattr(settings, "MCP_DISABLE_TOOL_RATE_LIMITS", False)),
            "tool_counts": tool_counts,
            "statuses": statuses,
            "error_codes": error_codes,
            "latency_ms": {"p50": p50, "p95": p95, "mean": mean, "max": max_latency},
            "rates": {"error_rate": error_rate, "throttled_rate": throttled_rate},
            "thresholds": {
                "p95_max_ms": max_p95_ms,
                "max_error_rate": max_error_rate,
                "max_throttled_rate": max_throttled_rate,
            },
            "violations": violations,
        }

        output_path_raw = str(options.get("output") or "").strip()
        output_path = Path(output_path_raw) if output_path_raw else self._default_output_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS("MCP load test completed"))
        self.stdout.write(f"business_id={business.id} conversation_id={conversation.id} mode={mode}")
        self.stdout.write(f"iterations={iterations} concurrency={concurrency} rate_limits_disabled={report['rate_limits_disabled']}")
        self.stdout.write(f"tool_counts={tool_counts}")
        self.stdout.write(f"statuses={statuses}")
        if error_codes:
            self.stdout.write(f"error_codes={error_codes}")
        if durations:
            self.stdout.write(
                "latency_ms:"
                f" p50={p50:.1f}"
                f" p95={p95:.1f}"
                f" mean={mean:.1f}"
                f" max={max_latency}"
            )
        else:
            self.stdout.write("latency_ms: no samples")
        self.stdout.write(self.style.SUCCESS(f"Exported load test artifacts to {output_path}"))

        if bool(options.get("enforce")) and violations:
            for key, info in violations.items():
                self.stderr.write(self.style.ERROR(f"{key} violated (obs={info.get('observed')} max={info.get('maximum')})"))
            raise CommandError("MCP load test thresholds failed")

    @staticmethod
    def _default_output_path() -> Path:
        base = getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "var" / "logs")
        return Path(base) / "mcp_load_test_latest.json"
