from __future__ import annotations

from typing import Mapping

from django.db import transaction
from django.db.models import Max

from apps.agent_runs.models import AgentRun, AgentRunEvent


class AgentRunQueueEventMixin:

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
