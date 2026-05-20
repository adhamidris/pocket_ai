from __future__ import annotations

import logging

from django.utils import timezone

from apps.agent_runs.execution.queue_claiming import AgentRunQueueClaimingMixin
from apps.agent_runs.execution.queue_events import AgentRunQueueEventMixin
from apps.agent_runs.execution.queue_retries import AgentRunQueueRetryMixin
from apps.agent_runs.execution.results import AgentRunProcessResult


logger = logging.getLogger(__name__)


class AgentRunQueueMixin(
    AgentRunQueueClaimingMixin,
    AgentRunQueueRetryMixin,
    AgentRunQueueEventMixin,
):

    def process_next_run(self) -> AgentRunProcessResult | None:
        self._expire_due_checkpoints(now=timezone.now())
        self._requeue_stale_running_runs(limit=self.max_stale_requeues_per_pass)
        run = self._claim_next_run()
        if not run:
            return None

        try:
            return self._execute_run(run)
        except Exception as exc:
            logger.exception("agent_run.execute_failed run=%s", run.id)
            return self._requeue_run_with_backoff(run, f"execution failed: {exc}", reason="execution_failed")
