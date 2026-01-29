from __future__ import annotations

import dataclasses
import hashlib
import logging
import time
from datetime import timedelta
from typing import Any, Mapping

from django.conf import settings
from django.db import connection as db_connection
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.tenancy import tenant_bypass, tenant_context

from apps.conversations.models import (
    AgentRun,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
)
from apps.conversations.output_destinations import parse_summary_destination_id, parse_summary_max_chars


logger = logging.getLogger(__name__)


def _clip_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    text = str(value)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _summarize_input_payload(payload: object) -> dict[str, object]:
    """
    Return a privacy-safe summary of tool arguments.

    Audit logs must avoid storing raw sensitive payloads (PII / document text).
    """

    if not isinstance(payload, Mapping):
        return {"redacted": True, "keys": []}
    keys = []
    for key in payload.keys():
        if not isinstance(key, str):
            continue
        key_norm = key.strip()
        if not key_norm:
            continue
        keys.append(key_norm[:80])
    keys = sorted(set(keys))
    return {
        "redacted": True,
        "keys": keys[:60],
        **({"keys_total": len(keys)} if len(keys) > 60 else {}),
    }


def _sanitize_remote_meta(remote: object) -> dict[str, object] | None:
    if not isinstance(remote, Mapping):
        return None
    out: dict[str, object] = {}
    conn_id = remote.get("connection_id")
    conn_name = remote.get("connection_name")
    remote_tool = remote.get("remote_tool") or remote.get("tool")
    if isinstance(conn_id, str) and conn_id.strip():
        out["connection_id"] = conn_id.strip()
    if isinstance(conn_name, str) and conn_name.strip():
        out["connection_name"] = _clip_text(conn_name.strip(), 240)
    if isinstance(remote_tool, str) and remote_tool.strip():
        out["remote_tool"] = _clip_text(remote_tool.strip(), 240)
    return out or None


def _sanitize_approval_meta(approval: object) -> dict[str, object] | None:
    """
    Store minimal approval metadata (no raw inputs/reasons).
    """

    if not isinstance(approval, Mapping):
        return None
    out: dict[str, object] = {}
    approval_id = approval.get("id")
    status = approval.get("status")
    mode = approval.get("mode")
    operation_type = approval.get("operation_type")
    expires_at = approval.get("expires_at")

    if isinstance(approval_id, str) and approval_id.strip():
        out["id"] = approval_id.strip()
    if isinstance(status, str) and status.strip():
        out["status"] = status.strip()[:64]
    if isinstance(mode, str) and mode.strip():
        out["mode"] = mode.strip()[:64]
    if isinstance(operation_type, str) and operation_type.strip():
        out["operation_type"] = operation_type.strip()[:64]
    if isinstance(expires_at, str) and expires_at.strip():
        out["expires_at"] = expires_at.strip()[:64]

    return out or None


def _sanitize_tool_output(tool_name: str, output: object) -> dict[str, object] | None:
    if not isinstance(output, Mapping):
        return None

    normalized = str(tool_name or "").strip().lower()
    status = str(output.get("status") or "").strip() or "ok"
    out: dict[str, object] = {"status": status}

    if status != "ok":
        error_code = str(output.get("error_code") or output.get("error") or "").strip()
        error = str(output.get("error") or "").strip()
        hint = str(output.get("hint") or "").strip()
        if error_code:
            out["error_code"] = _clip_text(error_code, 120)
        if error:
            out["error"] = _clip_text(error, 240)
        if hint:
            out["hint"] = _clip_text(hint, 240)
        return out

    # Email tools already return privacy-safe outputs in the MCP layer; keep a small allowlist.
    if normalized.startswith("email_"):
        safe_keys = {
            "status",
            "result_count",
            "message_ids",
            "thread_ids",
            "message_id",
            "thread_id",
            "draft_id",
            "message_count",
            "returned_messages",
            "truncated",
            "body_truncated",
        }
        for key in safe_keys:
            if key not in output:
                continue
            value = output.get(key)
            if key in {"truncated", "body_truncated"}:
                out[key] = bool(value)
            elif key in {"result_count", "message_count", "returned_messages"}:
                try:
                    out[key] = int(value or 0)
                except (TypeError, ValueError):
                    continue
            elif key in {"message_ids", "thread_ids"} and isinstance(value, list):
                out[key] = [str(v)[:160] for v in value[:10] if str(v or "").strip()]
            else:
                if isinstance(value, str) and value.strip():
                    out[key] = _clip_text(value.strip(), 240)
        return out

    if normalized == "create_agent_request":
        req_id = output.get("agent_request_id")
        if isinstance(req_id, str) and req_id.strip():
            out["agent_request_id"] = req_id.strip()
        request_payload = output.get("request") if isinstance(output.get("request"), Mapping) else None
        if request_payload:
            request_out: dict[str, object] = {}
            for key in ("id", "status", "subject"):
                value = request_payload.get(key)
                if isinstance(value, str) and value.strip():
                    request_out[key] = _clip_text(value.strip(), 240)
            from_agent = request_payload.get("from_agent") if isinstance(request_payload.get("from_agent"), Mapping) else None
            to_agent = request_payload.get("to_agent") if isinstance(request_payload.get("to_agent"), Mapping) else None
            if from_agent:
                from_out: dict[str, object] = {}
                for key in ("id", "name", "slug"):
                    value = from_agent.get(key)
                    if isinstance(value, str) and value.strip():
                        from_out[key] = _clip_text(value.strip(), 160)
                if from_out:
                    request_out["from_agent"] = from_out
            if to_agent:
                to_out: dict[str, object] = {}
                for key in ("id", "name", "slug"):
                    value = to_agent.get(key)
                    if isinstance(value, str) and value.strip():
                        to_out[key] = _clip_text(value.strip(), 160)
                if to_out:
                    request_out["to_agent"] = to_out
            if request_out:
                out["request"] = request_out
        return out

    if normalized == "request_user_input":
        questions = output.get("questions")
        if isinstance(questions, list):
            out["questions_count"] = len(questions)
        schema_payload = output.get("schema")
        if isinstance(schema_payload, Mapping) and schema_payload:
            out["schema_keys"] = sorted({str(k)[:80] for k in schema_payload.keys() if isinstance(k, str) and k.strip()})[:40]
        return out

    # Generic output summary: keep only stable metadata and identifiers, drop any free-text content.
    for key in ("tool", "tool_id", "artifact_id"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = _clip_text(value.strip(), 240)

    remote_meta = _sanitize_remote_meta(output.get("remote"))
    if remote_meta:
        out["remote"] = remote_meta

    for key in ("is_error", "truncated", "prompt_compact", "body_truncated", "content_truncated"):
        if key in output:
            out[key] = bool(output.get(key))

    for key in ("result_count", "results_count", "message_count", "returned_messages", "content_items_total"):
        if key in output:
            try:
                out[key] = int(output.get(key) or 0)
            except (TypeError, ValueError):
                continue

    for key in ("message_id", "thread_id", "draft_id"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = _clip_text(value.strip(), 240)
    for key in ("message_ids", "thread_ids"):
        value = output.get(key)
        if isinstance(value, list):
            out[key] = [str(v)[:160] for v in value[:10] if str(v or "").strip()]

    return out


def sanitize_tool_event_for_audit(event: Mapping[str, object]) -> dict[str, object]:
    """
    Privacy-safe persisted tool event payload.

    NOTE: the full tool output may still exist out-of-band (artifacts), but the
    executed log should remain safe to show to managers by default.
    """

    out: dict[str, object] = {"redacted": True}
    event_id = event.get("event_id")
    phase = event.get("phase")
    status = event.get("status")
    tool_call_id = event.get("tool_call_id")
    tool_name = event.get("tool_name")
    kind = event.get("kind")
    duration_ms = event.get("duration_ms")

    if isinstance(event_id, str) and event_id.strip():
        out["event_id"] = event_id.strip()[:128]
    if isinstance(phase, str) and phase.strip():
        out["phase"] = phase.strip()[:40]
    if isinstance(status, str) and status.strip():
        out["status"] = status.strip()[:64]
    if isinstance(tool_call_id, str) and tool_call_id.strip():
        out["tool_call_id"] = tool_call_id.strip()[:128]
    if isinstance(tool_name, str) and tool_name.strip():
        out["tool_name"] = tool_name.strip()[:200]
    if isinstance(kind, str) and kind.strip():
        out["kind"] = kind.strip()[:40]
    if duration_ms is not None:
        try:
            out["duration_ms"] = int(duration_ms)
        except (TypeError, ValueError):
            pass

    defaults_applied = event.get("defaults_applied")
    if isinstance(defaults_applied, list) and defaults_applied:
        out["defaults_applied"] = [str(v)[:80] for v in defaults_applied[:20] if str(v or "").strip()]

    remote_meta = _sanitize_remote_meta(event.get("remote"))
    if remote_meta:
        out["remote"] = remote_meta

    approval_meta = _sanitize_approval_meta(event.get("approval"))
    if approval_meta:
        out["approval"] = approval_meta

    if "input" in event:
        out["input"] = _summarize_input_payload(event.get("input"))

    sanitized_output = _sanitize_tool_output(str(tool_name or ""), event.get("output"))
    if sanitized_output:
        out["output"] = sanitized_output

    return out


@dataclasses.dataclass(frozen=True)
class AgentRunProcessResult:
    run_id: str
    status: str
    requeued: bool = False
    error: str | None = None


class AgentRunProcessingService:
    """
    Background worker service for AgentRuns.

    This is intentionally production-friendly: DB-backed queue + leasing,
    no in-memory singleton queues.
    """

    def __init__(
        self,
        *,
        lease_seconds: float = 60.0,
        max_retries_default: int = 5,
        max_stale_requeues_per_pass: int = 25,
        max_retry_delay_seconds: float = 900.0,
    ) -> None:
        self.lease_seconds = float(lease_seconds or 60.0)
        self.max_retries_default = max(1, int(max_retries_default or 5))
        self.max_stale_requeues_per_pass = max(1, int(max_stale_requeues_per_pass or 25))
        self.max_retry_delay_seconds = float(max_retry_delay_seconds or 900.0)
        self.max_running_per_business = max(0, int(getattr(settings, "AGENT_RUN_MAX_RUNNING_PER_BUSINESS", 0) or 0))
        self.capacity_backoff_seconds = max(0.0, float(getattr(settings, "AGENT_RUN_CAPACITY_BACKOFF_SECONDS", 15.0) or 0.0))
        self.disabled_backoff_seconds = max(0.0, float(getattr(settings, "AGENT_RUN_DISABLED_BACKOFF_SECONDS", 900.0) or 0.0))
        self.claim_scan_limit = max(1, int(getattr(settings, "AGENT_RUN_CLAIM_SCAN_LIMIT", 25) or 25))

    def process_next_run(self) -> AgentRunProcessResult | None:
        self._requeue_stale_running_runs(limit=self.max_stale_requeues_per_pass)
        run = self._claim_next_run()
        if not run:
            return None

        try:
            return self._execute_run(run)
        except Exception as exc:
            logger.exception("agent_run.execute_failed run=%s", run.id)
            return self._requeue_run_with_backoff(run, f"execution failed: {exc}", reason="execution_failed")

    def _defer_run(
        self,
        run: AgentRun,
        *,
        now,
        delay_seconds: float,
        label: str,
        reason: str,
        extra_payload: Mapping[str, object] | None = None,
    ) -> None:
        delay = max(1.0, float(delay_seconds or 0.0))
        run_after = now + timedelta(seconds=delay)
        AgentRun.objects.filter(id=run.id, status=AgentRunStatus.QUEUED).update(
            run_after=run_after,
            lease_expires_at=None,
            updated_at=now,
        )
        payload: dict[str, object] = {"reason": reason, "run_after": run_after.isoformat()}
        if extra_payload:
            payload.update(dict(extra_payload))
        self._append_event(
            run,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label=label,
            payload=payload,
        )

    def _claim_next_run(self) -> AgentRun | None:
        now = timezone.now()
        qs = (
            AgentRun.objects.filter(status=AgentRunStatus.QUEUED)
            .filter(Q(run_after__lte=now) | Q(run_after__isnull=True))
            .order_by("run_after", "created_at")
        )

        supports_skip_locked = bool(
            getattr(db_connection.features, "has_select_for_update", False)
            and getattr(db_connection.features, "has_select_for_update_skip_locked", False)
        )
        supports_for_update_of = bool(getattr(db_connection.features, "has_select_for_update_of", False))
        supports_for_update = bool(getattr(db_connection.features, "has_select_for_update", False))

        from django.db.models import Count

        from apps.accounts.feature_flags import FeatureFlagService
        from apps.accounts.models import BusinessProfile

        scan_limit = max(1, int(self.claim_scan_limit or 1))
        max_running = max(0, int(self.max_running_per_business or 0))

        with tenant_bypass():
            with transaction.atomic():
                candidates: list[AgentRun] = []
                if supports_for_update:
                    for_update_kwargs: dict[str, Any] = {}
                    if supports_skip_locked:
                        for_update_kwargs["skip_locked"] = True
                    if supports_for_update_of:
                        for_update_kwargs["of"] = ("self",)
                    candidates = list(qs.select_for_update(**for_update_kwargs)[:scan_limit])
                else:
                    candidates = list(qs[:scan_limit])

                if not candidates:
                    return None

                business_ids = {run.business_profile_id for run in candidates if run.business_profile_id}
                enabled_by_business: dict[object, bool] = {}
                if business_ids:
                    for business in BusinessProfile.objects.filter(id__in=business_ids).only("id", "metadata"):
                        enabled_by_business[business.id] = bool(getattr(FeatureFlagService.snapshot(business), "sub_agents_v1", False))

                running_by_business: dict[object, int] = {}
                if max_running > 0 and business_ids:
                    for row in (
                        AgentRun.objects.filter(status=AgentRunStatus.RUNNING, business_profile_id__in=business_ids)
                        .values("business_profile_id")
                        .annotate(count=Count("id"))
                    ):
                        bid = row.get("business_profile_id")
                        running_by_business[bid] = int(row.get("count") or 0)

                for run in candidates:
                    business_id = run.business_profile_id
                    if not business_id:
                        self._defer_run(
                            run,
                            now=now,
                            delay_seconds=self.capacity_backoff_seconds,
                            label="Queued (missing business)",
                            reason="missing_business_profile_id",
                        )
                        continue

                    if not enabled_by_business.get(business_id, False):
                        self._defer_run(
                            run,
                            now=now,
                            delay_seconds=self.disabled_backoff_seconds,
                            label="Queued (sub-agents disabled)",
                            reason="sub_agents_disabled",
                        )
                        continue

                    if max_running > 0:
                        running = int(running_by_business.get(business_id, 0))
                        if running >= max_running:
                            self._defer_run(
                                run,
                                now=now,
                                delay_seconds=self.capacity_backoff_seconds,
                                label="Queued (capacity limit reached)",
                                reason="capacity_limit",
                                extra_payload={"running": running, "max": max_running},
                            )
                            continue
                        # Reserve a slot for this claim within this transaction.
                        running_by_business[business_id] = running + 1

                    lease = now + timedelta(seconds=max(10.0, float(self.lease_seconds)))
                    if supports_for_update:
                        run.status = AgentRunStatus.RUNNING
                        run.started_at = now
                        run.run_after = None
                        run.lease_expires_at = lease
                        run.save(update_fields=["status", "started_at", "run_after", "lease_expires_at", "updated_at"])
                    else:
                        updated = AgentRun.objects.filter(id=run.id, status=AgentRunStatus.QUEUED).update(
                            status=AgentRunStatus.RUNNING,
                            started_at=now,
                            run_after=None,
                            lease_expires_at=lease,
                        )
                        if not updated:
                            continue
                        run.refresh_from_db()

                    self._append_event(
                        run,
                        stream=AgentRunEventStream.SYSTEM,
                        event_type=AgentRunEventType.PROGRESS,
                        label="Started",
                        payload={"status": AgentRunStatus.RUNNING},
                    )
                    return run
                return None

    def _requeue_stale_running_runs(self, *, limit: int) -> int:
        now = timezone.now()
        cutoff = now - timedelta(seconds=max(10.0, float(self.lease_seconds)))
        with tenant_bypass():
            stale = list(
                AgentRun.objects.filter(status=AgentRunStatus.RUNNING)
                .filter(Q(lease_expires_at__lt=now) | Q(lease_expires_at__isnull=True, started_at__lt=cutoff))
                .order_by("started_at")[: max(1, int(limit))]
            )
        if not stale:
            return 0
        for run in stale:
            self._requeue_run_with_backoff(run, "auto-requeue: run lease expired", reason="lease_expired")
        return len(stale)

    def _job_retry_delay_seconds(self, run_id: str, attempt_count: int) -> float:
        normalized_attempt = max(1, int(attempt_count))
        base = min(self.max_retry_delay_seconds, float(2 ** min(10, normalized_attempt)))
        return base + self._deterministic_jitter(run_id, attempt_count)

    @staticmethod
    def _deterministic_jitter(run_id: str, attempt_count: int) -> float:
        """
        Deterministic jitter to avoid retry thundering herds without introducing non-determinism.
        """

        seed = f"{run_id}:{int(attempt_count)}".encode("utf-8", errors="ignore")
        digest = hashlib.sha256(seed).digest()
        # 0.0 .. < 1.0
        return int.from_bytes(digest[:2], "big") / 65536.0

    def _requeue_run_with_backoff(self, run: AgentRun, message: str, *, reason: str) -> AgentRunProcessResult:
        business_id = getattr(run, "business_profile_id", None)
        ctx = tenant_context(business_id) if business_id else tenant_bypass()
        with ctx:
            now = timezone.now()
            max_attempts = max(1, int(getattr(run, "max_attempts", 0) or 0) or self.max_retries_default)
            next_attempt = max(0, int(getattr(run, "attempt_count", 0) or 0)) + 1

            metadata = dict(run.metadata or {}) if isinstance(run.metadata, dict) else {}
            attempts = metadata.get("attempts")
            if not isinstance(attempts, list):
                attempts = []
            attempts.append(
                {
                    "attempt": next_attempt,
                    "at": now.isoformat(),
                    "reason": reason,
                    "error": (message or "")[:400],
                }
            )
            metadata["attempts"] = attempts[-10:]

            if next_attempt < max_attempts:
                delay = min(self.max_retry_delay_seconds, self._job_retry_delay_seconds(str(run.id), next_attempt))
                run_after = now + timedelta(seconds=float(delay))
                AgentRun.objects.filter(id=run.id).update(
                    status=AgentRunStatus.QUEUED,
                    attempt_count=next_attempt,
                    run_after=run_after,
                    lease_expires_at=None,
                    finished_at=None,
                    error_detail=(message or "")[:2000],
                    metadata=metadata,
                )
                self._append_event(
                    run,
                    stream=AgentRunEventStream.SYSTEM,
                    event_type=AgentRunEventType.PROGRESS,
                    label="Requeued",
                    payload={"reason": reason, "run_after": run_after.isoformat()},
                )
                return AgentRunProcessResult(run_id=str(run.id), status=AgentRunStatus.QUEUED, requeued=True, error=message)

            self._mark_run_failed_terminal(run, message, reason=reason, attempt_count=next_attempt, metadata=metadata)
            return AgentRunProcessResult(run_id=str(run.id), status=AgentRunStatus.FAILED, requeued=False, error=message)

    def _mark_run_failed_terminal(
        self,
        run: AgentRun,
        message: str,
        *,
        reason: str,
        attempt_count: int,
        metadata: dict[str, object] | None = None,
    ) -> None:
        now = timezone.now()
        AgentRun.objects.filter(id=run.id).update(
            status=AgentRunStatus.FAILED,
            attempt_count=attempt_count,
            run_after=None,
            lease_expires_at=None,
            finished_at=now,
            error_detail=(message or "")[:2000],
            metadata=metadata or {},
        )
        self._append_event(
            run,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.ERROR,
            label="Failed",
            payload={"reason": reason, "error": (message or "")[:500]},
        )

    def _append_event(
        self,
        run: AgentRun,
        *,
        stream: str,
        event_type: str,
        label: str,
        payload: Mapping[str, object] | None = None,
    ) -> AgentRunEvent:
        # Serialize event ordering via the run row lock so API-driven events (cancel, etc.)
        # can't collide with worker event writes.
        with transaction.atomic():
            locked_run = AgentRun.objects.select_for_update().get(id=run.id)
            next_index = (
                AgentRunEvent.objects.filter(run=locked_run).aggregate(max_index=Max("sequence_index")).get("max_index") or 0
            )
            return AgentRunEvent.objects.create(
                run=locked_run,
                sequence_index=int(next_index) + 1,
                stream=stream,
                event_type=event_type,
                label=(label or "")[:240],
                payload=dict(payload or {}),
            )

    def _execute_run(self, run: AgentRun) -> AgentRunProcessResult:
        """
        Minimal executor: run one MCP turn using the run's goal and snapshot.

        This is intentionally conservative:
        - Uses the agent's MCP orchestrator (tool loop) for deterministic server-side tool execution.
        - Persists tool/status events as AgentRunEvents.
        - Stores the final response in AgentRun.result.
        """

        from apps.llm.llm_provider import load_mcp_provider
        from apps.mcp.orchestrator import McpOrchestratorService
        from apps.conversations.content_blocks import ensure_assistant_text_blocks
        from apps.conversations.models import Conversation, ConversationChannel, ConversationMessage, ConversationSender

        business_id = run.business_profile_id
        if not business_id:
            raise RuntimeError("run missing business_profile_id")

        # Rollout guard: do not execute runs when sub-agents are disabled for this tenant.
        try:
            from apps.accounts.feature_flags import FeatureFlagService
            from apps.accounts.models import BusinessProfile

            with tenant_context(business_id):
                business = BusinessProfile.objects.filter(id=business_id).only("id", "metadata").first()
            enabled = bool(getattr(FeatureFlagService.snapshot(business), "sub_agents_v1", False)) if business else False
        except Exception:  # pragma: no cover - best effort only
            enabled = False

        if not enabled:
            with tenant_context(business_id):
                now = timezone.now()
                run_after = now + timedelta(seconds=max(1.0, float(self.disabled_backoff_seconds or 0.0)))
                AgentRun.objects.filter(id=run.id).update(
                    status=AgentRunStatus.QUEUED,
                    run_after=run_after,
                    lease_expires_at=None,
                    finished_at=None,
                    error_detail="",
                    updated_at=now,
                )
                self._append_event(
                    run,
                    stream=AgentRunEventStream.SYSTEM,
                    event_type=AgentRunEventType.PROGRESS,
                    label="Queued (sub-agents disabled)",
                    payload={"reason": "sub_agents_disabled", "run_after": run_after.isoformat()},
                )
            return AgentRunProcessResult(
                run_id=str(run.id),
                status=AgentRunStatus.QUEUED,
                requeued=True,
                error="sub_agents_disabled",
            )

        provider = load_mcp_provider()
        if provider is None:
            raise RuntimeError("MCP provider is not configured.")

        started = time.monotonic()
        spec = dict(run.run_spec_snapshot or {}) if isinstance(run.run_spec_snapshot, dict) else {}
        goal = str(spec.get("goal") or spec.get("name") or run.title or "").strip()
        if not goal:
            raise RuntimeError("run has no goal/title to execute")

        allowlist_specified = "tool_allowlist" in spec
        tool_allowlist = spec.get("tool_allowlist")
        allowed_tools: set[str] | None = None
        if isinstance(tool_allowlist, list):
            allowed_tools = {str(value).strip() for value in tool_allowlist if str(value or "").strip()}

        timeout_seconds = None
        constraints = spec.get("constraints")
        if isinstance(constraints, Mapping):
            raw_timeout = constraints.get("timeout_seconds")
            try:
                timeout_seconds = int(raw_timeout) if raw_timeout is not None else None
            except (TypeError, ValueError):
                timeout_seconds = None
        if timeout_seconds is None:
            timeout_seconds = 300
        timeout_seconds = max(10, min(int(timeout_seconds), 1800))

        with tenant_context(business_id):
            conversation = run.conversation
            created_conversation = False
            if conversation is None:
                conversation_metadata: dict[str, object] = {"source": "agent_run", "agent_run_id": str(run.id)}
                if run.created_by_id:
                    conversation_metadata["actor_user_id"] = str(run.created_by_id)
                conversation = Conversation.objects.create(
                    business_profile_id=business_id,
                    agent_profile_id=run.agent_profile_id,
                    channel=ConversationChannel.API,
                    metadata=conversation_metadata,
                )
                AgentRun.objects.filter(id=run.id).update(conversation_id=conversation.id, updated_at=timezone.now())
                run.conversation = conversation
                created_conversation = True

            orchestrator = McpOrchestratorService(agent=run.agent_profile, provider=provider)

            pause_state: dict[str, object] = {"approval_event": None, "user_input_event": None, "external_request_event": None}

            def _deadline_exceeded() -> bool:
                return (time.monotonic() - started) >= float(timeout_seconds or 300)

            def _should_cancel() -> bool:
                if _deadline_exceeded():
                    return True
                # Allow external cancellation (best effort; cheap check).
                status_now = (
                    AgentRun.objects.filter(id=run.id).values_list("status", flat=True).first()
                )
                return status_now == AgentRunStatus.CANCELLED

            def _on_status_change(state: object) -> None:
                if not state:
                    return
                if isinstance(state, str):
                    code = state.strip()
                    if code:
                        self._append_event(
                            run,
                            stream=AgentRunEventStream.SYSTEM,
                            event_type=AgentRunEventType.PROGRESS,
                            label=code,
                            payload={"code": code},
                        )
                    return
                if isinstance(state, Mapping):
                    code = state.get("code") or state.get("state")
                    label = state.get("label")
                    meta = state.get("meta") if isinstance(state.get("meta"), Mapping) else None
                    code_str = str(code).strip() if isinstance(code, str) else ""
                    label_str = str(label).strip() if isinstance(label, str) else ""
                    payload: dict[str, object] = {}
                    if code_str:
                        payload["code"] = code_str
                    if label_str:
                        payload["label"] = label_str
                    if meta:
                        payload["meta"] = dict(meta)
                    if payload:
                        self._append_event(
                            run,
                            stream=AgentRunEventStream.SYSTEM,
                            event_type=AgentRunEventType.PROGRESS,
                            label=label_str or code_str or "status",
                            payload=payload,
                        )

            def _on_tool_event(event: Mapping[str, object] | None) -> None:
                if not event:
                    return
                phase = str(event.get("phase") or "").strip().lower()
                tool_name = str(event.get("tool_name") or "").strip()
                status_value = str(event.get("status") or "").strip().lower()
                if phase == "approval_requested" and status_value == "pending_approval":
                    pause_state["approval_event"] = dict(event)
                if tool_name == "request_user_input" and phase in {"started", "finished"}:
                    pause_state["user_input_event"] = dict(event)
                label = f"{phase}:{tool_name}" if tool_name else (phase or "tool_event")
                if tool_name == "create_agent_request" and phase == "finished":
                    if status_value == "needs_external":
                        pause_state["external_request_event"] = dict(event)
                    output = event.get("output") if isinstance(event.get("output"), Mapping) else {}
                    request_payload = (
                        output.get("request") if isinstance(output, Mapping) and isinstance(output.get("request"), Mapping) else {}
                    )
                    from_agent = request_payload.get("from_agent") if isinstance(request_payload, Mapping) else {}
                    to_agent = request_payload.get("to_agent") if isinstance(request_payload, Mapping) else {}
                    from_name = str(from_agent.get("name") or "").strip() if isinstance(from_agent, Mapping) else ""
                    to_name = str(to_agent.get("name") or "").strip() if isinstance(to_agent, Mapping) else ""
                    subject = str(request_payload.get("subject") or "").strip() if isinstance(request_payload, Mapping) else ""
                    prefix = "Agent request"
                    if from_name and to_name:
                        prefix = f"{from_name} → {to_name}" if from_name != to_name else from_name
                    elif to_name:
                        prefix = f"Agent → {to_name}"
                    label = f"{prefix}: {subject}" if subject else prefix
                self._append_event(
                    run,
                    stream=AgentRunEventStream.EXECUTED,
                    event_type=AgentRunEventType.PROGRESS,
                    label=label[:240],
                    payload=sanitize_tool_event_for_audit(event),
                )

            allowlist_display: object
            if allowlist_specified:
                allowlist_display = sorted(allowed_tools) if allowed_tools else []
            else:
                allowlist_display = "default"

            metadata_snapshot = run.metadata if isinstance(getattr(run, "metadata", None), Mapping) else {}
            external_inputs_summary = ""
            raw_external_inputs = metadata_snapshot.get("external_inputs") if isinstance(metadata_snapshot, Mapping) else None
            if isinstance(raw_external_inputs, list) and raw_external_inputs:
                lines: list[str] = []
                for item in raw_external_inputs[-3:]:
                    if not isinstance(item, Mapping):
                        continue
                    subject = str(item.get("subject") or "").strip()
                    resolution = str(item.get("resolution") or "").strip()
                    if resolution:
                        resolution = resolution[:800].rstrip()
                    if subject and resolution:
                        lines.append(f"- {subject}: {resolution}")
                    elif subject:
                        lines.append(f"- {subject}")
                    elif resolution:
                        lines.append(f"- {resolution}")
                if lines:
                    external_inputs_summary = "External inputs:\n" + "\n".join(lines) + "\n"

            trigger_context_summary = ""
            raw_trigger = metadata_snapshot.get("trigger") if isinstance(metadata_snapshot, Mapping) else None
            if isinstance(raw_trigger, Mapping) and raw_trigger:
                lines: list[str] = []
                preferred_keys = [
                    "type",
                    "provider",
                    "email_account_id",
                    "message_id",
                    "thread_id",
                    "from",
                    "to",
                    "subject",
                    "date",
                    "snippet",
                ]
                for key in preferred_keys:
                    value = raw_trigger.get(key)
                    if value is None or value == "":
                        continue
                    text = str(value).strip()
                    if not text:
                        continue
                    if key == "snippet":
                        text = text[:800].rstrip()
                    else:
                        text = text[:240].rstrip()
                    lines.append(f"- {key}: {text}")
                if lines:
                    trigger_context_summary = "Trigger context:\n" + "\n".join(lines) + "\n"

            user_message = (
                "You are running a background task (agent run).\n"
                f"Goal: {goal}\n"
                f"Success criteria: {spec.get('success_criteria') or []}\n"
                f"Constraints: {spec.get('constraints') or {}}\n"
                f"Tool allowlist: {allowlist_display}\n"
                f"{trigger_context_summary}"
                f"{external_inputs_summary}"
                "Instructions:\n"
                "- Work autonomously.\n"
                "- If you require missing information from the user, call request_user_input with concise questions and stop.\n"
                "- If you require another agent/department, call create_agent_request with a subject + question + context_refs and stop.\n"
                "- If a tool call is pending approval, ask the user to approve/deny and stop.\n"
                "- Do not claim actions happened unless they were executed via tools.\n"
                "- Keep internal steps/tool chatter out of the final response.\n"
            )

            turn = orchestrator.stream_turn(
                conversation=conversation,
                user_message=user_message,
                on_status_change=_on_status_change,
                on_tool_event=_on_tool_event,
                should_cancel=_should_cancel,
                allowed_tools=allowed_tools,
                wait_for_tool_approval=False,
            )

            if _deadline_exceeded():
                raise RuntimeError("timeout: run exceeded configured timeout_seconds")

            approval_event = pause_state.get("approval_event") if isinstance(pause_state, dict) else None
            user_input_event = pause_state.get("user_input_event") if isinstance(pause_state, dict) else None
            external_request_event = pause_state.get("external_request_event") if isinstance(pause_state, dict) else None

            next_status = AgentRunStatus.COMPLETED
            pause_event_type: str | None = None
            pause_payload: dict[str, object] | None = None

            approval_payload: dict[str, object] | None = None
            approval_id = ""
            if isinstance(approval_event, Mapping):
                approval_payload = (
                    approval_event.get("approval") if isinstance(approval_event.get("approval"), Mapping) else None
                )
                approval_id = str(approval_payload.get("id") if approval_payload else "" or "").strip()

            user_input_payload: dict[str, object] | None = None
            if isinstance(user_input_event, Mapping):
                user_input_payload = (
                    user_input_event.get("input") if isinstance(user_input_event.get("input"), Mapping) else None
                )

            external_request_id = ""
            external_request_payload: dict[str, object] | None = None
            if isinstance(external_request_event, Mapping):
                output = external_request_event.get("output") if isinstance(external_request_event.get("output"), Mapping) else None
                if isinstance(output, Mapping):
                    external_request_id = str(output.get("agent_request_id") or "").strip()
                    request_payload = output.get("request") if isinstance(output.get("request"), Mapping) else None
                    if not external_request_id and isinstance(request_payload, Mapping):
                        external_request_id = str(request_payload.get("id") or "").strip()
                        external_request_payload = dict(request_payload)
                    elif isinstance(request_payload, Mapping):
                        external_request_payload = dict(request_payload)

            if approval_id:
                next_status = AgentRunStatus.WAITING_APPROVAL
                pause_event_type = AgentRunEventType.NEEDS_APPROVAL
                pause_payload = {
                    "approval": dict(approval_payload) if approval_payload else {},
                    "tool_name": str(approval_event.get("tool_name") or "") if isinstance(approval_event, Mapping) else "",
                    "remote": dict(approval_event.get("remote") or {}) if isinstance(approval_event, Mapping) and isinstance(approval_event.get("remote"), Mapping) else {},
                }
            elif user_input_payload:
                next_status = AgentRunStatus.WAITING_USER
                pause_event_type = AgentRunEventType.NEEDS_USER
                pause_payload = {
                    "questions": list(user_input_payload.get("questions") or ())
                    if isinstance(user_input_payload.get("questions"), list)
                    else [],
                    "prompt": str(user_input_payload.get("prompt") or "").strip(),
                    "schema": dict(user_input_payload.get("schema") or {})
                    if isinstance(user_input_payload.get("schema"), Mapping)
                    else {},
                }
            elif external_request_id:
                next_status = AgentRunStatus.WAITING_EXTERNAL

            now = timezone.now()
            base_result = {
                "response_text": turn.response_text,
                "response_blocks": list(turn.response_blocks or ()),
                "planned_actions": [dataclasses.asdict(a) for a in (turn.planned_actions or ())] if turn.planned_actions else [],
                "extractions": [dataclasses.asdict(e) for e in (turn.extractions or ())] if turn.extractions else [],
                "llm_usage": dict(turn.llm_usage or {}) if getattr(turn, "llm_usage", None) else None,
                "tool_trace": list(turn.tool_trace or ()),
            }

            next_metadata = dict(run.metadata or {}) if isinstance(getattr(run, "metadata", None), dict) else {}
            if next_status == AgentRunStatus.WAITING_APPROVAL and approval_id:
                next_metadata["pending_approval_id"] = approval_id
            if next_status == AgentRunStatus.WAITING_USER and isinstance(pause_payload, dict):
                next_metadata["pending_user_input"] = dict(pause_payload)
            if next_status == AgentRunStatus.WAITING_EXTERNAL and external_request_id:
                next_metadata["pending_agent_request_id"] = external_request_id
                if external_request_payload:
                    next_metadata["pending_agent_request"] = external_request_payload

            update_fields: dict[str, object] = {
                "status": next_status,
                "lease_expires_at": None,
                "run_after": None,
                "error_detail": "",
                "result": base_result,
                "metadata": next_metadata,
                "updated_at": now,
            }
            if next_status == AgentRunStatus.COMPLETED:
                update_fields["finished_at"] = now

            updated = AgentRun.objects.filter(id=run.id, status=AgentRunStatus.RUNNING).update(**update_fields)
            if not updated:
                status_now = AgentRun.objects.filter(id=run.id).values_list("status", flat=True).first() or ""
                return AgentRunProcessResult(run_id=str(run.id), status=str(status_now) or "unknown")

            if next_status == AgentRunStatus.COMPLETED:
                self._append_event(
                    run,
                    stream=AgentRunEventStream.SYSTEM,
                    event_type=AgentRunEventType.RESULT,
                    label="Completed",
                    payload={"status": AgentRunStatus.COMPLETED},
                )
                if run.source in {AgentRunSource.AUTOMATION, AgentRunSource.WATCHER}:
                    response_text = str(turn.response_text or "").strip()
                    if response_text:
                        already_written = ConversationMessage.objects.filter(
                            conversation_id=conversation.id,
                            metadata__agent_run_id=str(run.id),
                            metadata__type="run_result",
                        ).exists()
                        if not already_written:
                            ConversationMessage.objects.create(
                                conversation=conversation,
                                sender=ConversationSender.AI,
                                body=response_text,
                                metadata={
                                    "source": "agent_run",
                                    "agent_run_id": str(run.id),
                                    "type": "run_result",
                                    "run_source": run.source,
                                },
                                content_blocks=ensure_assistant_text_blocks(response_text),
                            )
                            Conversation.objects.filter(id=conversation.id).update(last_activity_at=now)

                        dest_cfg = next_metadata.get("destination_config") if isinstance(next_metadata, Mapping) else None
                        summary_conversation_id = parse_summary_destination_id(dest_cfg)
                        if summary_conversation_id:
                            summary_max = parse_summary_max_chars(dest_cfg, default=800)
                            summary_text = response_text[:summary_max].rstrip()
                            if summary_text:
                                target = Conversation.objects.filter(
                                    id=summary_conversation_id,
                                    business_profile_id=business_id,
                                ).first()
                                if target is not None:
                                    already_summary = ConversationMessage.objects.filter(
                                        conversation_id=target.id,
                                        metadata__agent_run_id=str(run.id),
                                        metadata__type="run_summary",
                                    ).exists()
                                    if not already_summary:
                                        ConversationMessage.objects.create(
                                            conversation=target,
                                            sender=ConversationSender.AI,
                                            body=summary_text,
                                            metadata={
                                                "source": "agent_run",
                                                "agent_run_id": str(run.id),
                                                "type": "run_summary",
                                                "run_source": run.source,
                                                "destination": "chat_thread",
                                            },
                                            content_blocks=ensure_assistant_text_blocks(summary_text),
                                        )
                                        Conversation.objects.filter(id=target.id).update(last_activity_at=now)
            elif next_status == AgentRunStatus.WAITING_EXTERNAL and external_request_id:
                self._append_event(
                    run,
                    stream=AgentRunEventStream.SYSTEM,
                    event_type=AgentRunEventType.PROGRESS,
                    label="Waiting for agent response",
                    payload={"agent_request_id": external_request_id},
                )
            elif pause_event_type and pause_payload is not None:
                self._append_event(
                    run,
                    stream=AgentRunEventStream.SYSTEM,
                    event_type=pause_event_type,
                    label="Needs approval" if next_status == AgentRunStatus.WAITING_APPROVAL else "Needs user input",
                    payload=dict(pause_payload),
                )

                prompt_text = str(turn.response_text or "").strip()
                if not prompt_text:
                    if next_status == AgentRunStatus.WAITING_APPROVAL:
                        tool_label = (
                            str(pause_payload.get("tool_name") or "").strip()
                            if isinstance(pause_payload, Mapping)
                            else ""
                        )
                        prompt_text = (
                            "This task needs your approval to continue."
                            + (f" Tool: {tool_label}." if tool_label else "")
                            + " Please approve/deny from the Tasks panel."
                        )
                    else:
                        questions = []
                        if isinstance(pause_payload, Mapping):
                            raw_questions = pause_payload.get("questions")
                            if isinstance(raw_questions, list):
                                questions = [str(q).strip() for q in raw_questions if str(q or "").strip()]
                        if questions:
                            bullets = "\n".join([f"- {q}" for q in questions[:6]])
                            prompt_text = (
                                "I need a bit more info to continue:\n"
                                f"{bullets}\n\n"
                                "Please reply from the Tasks panel."
                            )
                        else:
                            prompt_text = "I need a bit more info to continue. Please reply from the Tasks panel."

                ConversationMessage.objects.create(
                    conversation=conversation,
                    sender=ConversationSender.AI,
                    body=prompt_text,
                    metadata={
                        "source": "agent_run",
                        "agent_run_id": str(run.id),
                        "type": "needs_approval" if next_status == AgentRunStatus.WAITING_APPROVAL else "needs_user",
                    },
                    content_blocks=ensure_assistant_text_blocks(prompt_text),
                )

            if created_conversation and next_status == AgentRunStatus.COMPLETED:
                # Persist a minimal conversation message when we had to create a thread implicitly.
                response_text = str(turn.response_text or "").strip()
                if response_text:
                    ConversationMessage.objects.create(
                        conversation=conversation,
                        sender=ConversationSender.AI,
                        body=response_text,
                        metadata={"source": "agent_run", "agent_run_id": str(run.id)},
                        content_blocks=ensure_assistant_text_blocks(response_text),
                    )

        return AgentRunProcessResult(run_id=str(run.id), status=next_status)
