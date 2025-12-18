from __future__ import annotations

import math
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from django.core.management.base import BaseCommand
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

    def handle(self, *args, **options):
        business_id_raw = str(options.get("business_id") or "").strip()
        try:
            business_id = uuid.UUID(business_id_raw)
        except (TypeError, ValueError):
            self.stderr.write(self.style.ERROR("Invalid --business-id"))
            return
        business = BusinessProfile.objects.filter(id=business_id).first()
        if not business:
            self.stderr.write(self.style.ERROR("BusinessProfile not found."))
            return

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
                self.stderr.write(self.style.ERROR("--document-id is required for mode=read_knowledge"))
                return
            try:
                uuid.UUID(document_id_raw)
            except (TypeError, ValueError):
                self.stderr.write(self.style.ERROR("Invalid --document-id UUID"))
                return

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

        durations = [r.duration_ms for r in results if r.duration_ms > 0]
        statuses: dict[str, int] = {}
        error_codes: dict[str, int] = {}
        tool_counts: dict[str, int] = {}
        for r in results:
            statuses[r.status] = statuses.get(r.status, 0) + 1
            tool_counts[r.tool] = tool_counts.get(r.tool, 0) + 1
            if r.error_code:
                error_codes[r.error_code] = error_codes.get(r.error_code, 0) + 1

        self.stdout.write(self.style.SUCCESS("MCP load test completed"))
        self.stdout.write(f"business_id={business.id} conversation_id={conversation.id} mode={mode}")
        self.stdout.write(f"iterations={iterations} concurrency={concurrency}")
        self.stdout.write(f"tool_counts={tool_counts}")
        self.stdout.write(f"statuses={statuses}")
        if error_codes:
            self.stdout.write(f"error_codes={error_codes}")
        if durations:
            self.stdout.write(
                "latency_ms:"
                f" p50={_percentile(durations, 50):.1f}"
                f" p95={_percentile(durations, 95):.1f}"
                f" mean={statistics.mean(durations):.1f}"
                f" max={max(durations)}"
            )
        else:
            self.stdout.write("latency_ms: no samples")

