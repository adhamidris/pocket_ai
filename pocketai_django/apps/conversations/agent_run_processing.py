from __future__ import annotations

import dataclasses
import hashlib
import logging
import time
from datetime import timedelta
from typing import Any, Mapping

from django.db import connection as db_connection
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.tenancy import tenant_context

from apps.conversations.models import (
    AgentRun,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunStatus,
)


logger = logging.getLogger(__name__)


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

        with transaction.atomic():
            run = None
            if supports_for_update:
                for_update_kwargs: dict[str, Any] = {}
                if supports_skip_locked:
                    for_update_kwargs["skip_locked"] = True
                if supports_for_update_of:
                    for_update_kwargs["of"] = ("self",)
                run = qs.select_for_update(**for_update_kwargs).first()
            else:
                run = qs.first()
            if not run:
                return None

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
                    return None
                run.refresh_from_db()

            self._append_event(
                run,
                stream=AgentRunEventStream.SYSTEM,
                event_type=AgentRunEventType.PROGRESS,
                label="Started",
                payload={"status": AgentRunStatus.RUNNING},
            )
            return run

    def _requeue_stale_running_runs(self, *, limit: int) -> int:
        now = timezone.now()
        cutoff = now - timedelta(seconds=max(10.0, float(self.lease_seconds)))
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
        from apps.conversations.models import Conversation, ConversationChannel, ConversationMessage, ConversationSender

        business_id = run.business_profile_id
        if not business_id:
            raise RuntimeError("run missing business_profile_id")

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
                label = f"{phase}:{tool_name}" if tool_name else (phase or "tool_event")
                self._append_event(
                    run,
                    stream=AgentRunEventStream.EXECUTED,
                    event_type=AgentRunEventType.PROGRESS,
                    label=label[:240],
                    payload=dict(event),
                )

            allowlist_display: object
            if allowlist_specified:
                allowlist_display = sorted(allowed_tools) if allowed_tools else []
            else:
                allowlist_display = "default"

            user_message = (
                "You are running a background task (agent run).\n"
                f"Goal: {goal}\n"
                f"Success criteria: {spec.get('success_criteria') or []}\n"
                f"Constraints: {spec.get('constraints') or {}}\n"
                f"Tool allowlist: {allowlist_display}\n"
                "Instructions:\n"
                "- Work autonomously.\n"
                "- If you require missing information, ask concise questions.\n"
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
            )

            if _deadline_exceeded():
                raise RuntimeError("timeout: run exceeded configured timeout_seconds")

            updated = AgentRun.objects.filter(id=run.id, status=AgentRunStatus.RUNNING).update(
                status=AgentRunStatus.COMPLETED,
                finished_at=timezone.now(),
                lease_expires_at=None,
                run_after=None,
                error_detail="",
                result={
                    "response_text": turn.response_text,
                    "response_blocks": list(turn.response_blocks or ()),
                    "planned_actions": [dataclasses.asdict(a) for a in (turn.planned_actions or ())] if turn.planned_actions else [],
                    "extractions": [dataclasses.asdict(e) for e in (turn.extractions or ())] if turn.extractions else [],
                    "llm_usage": dict(turn.llm_usage or {}) if getattr(turn, "llm_usage", None) else None,
                    "tool_trace": list(turn.tool_trace or ()),
                },
                updated_at=timezone.now(),
            )
            if not updated:
                status_now = AgentRun.objects.filter(id=run.id).values_list("status", flat=True).first() or ""
                return AgentRunProcessResult(run_id=str(run.id), status=str(status_now) or "unknown")
            self._append_event(
                run,
                stream=AgentRunEventStream.SYSTEM,
                event_type=AgentRunEventType.RESULT,
                label="Completed",
                payload={"status": AgentRunStatus.COMPLETED},
            )

            if created_conversation:
                # Persist a minimal conversation message when we had to create a thread implicitly.
                ConversationMessage.objects.create(
                    conversation=conversation,
                    sender=ConversationSender.AI,
                    body=str(turn.response_text or ""),
                    metadata={"source": "agent_run", "agent_run_id": str(run.id)},
                    content_blocks=[],
                )

        return AgentRunProcessResult(run_id=str(run.id), status=AgentRunStatus.COMPLETED)
