from __future__ import annotations

from django.conf import settings

from apps.agent_runs.execution.audit import (
    sanitize_tool_event_for_audit,
)
from apps.agent_runs.execution.checkpoints import AgentRunCheckpointMixin
from apps.agent_runs.execution.pending_tools import AgentRunPendingToolMixin
from apps.agent_runs.execution.queue import AgentRunQueueMixin
from apps.agent_runs.execution.results import AgentRunProcessResult
from apps.agent_runs.execution.runner import AgentRunExecutorMixin
from apps.agent_runs.execution.workflow_memory import AgentRunWorkflowMemoryMixin


class AgentRunProcessingService(
    AgentRunQueueMixin,
    AgentRunCheckpointMixin,
    AgentRunWorkflowMemoryMixin,
    AgentRunPendingToolMixin,
    AgentRunExecutorMixin,
):
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
