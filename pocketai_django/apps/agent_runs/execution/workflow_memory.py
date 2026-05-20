from __future__ import annotations

from apps.agent_runs.execution.run_reports import AgentRunReportMixin
from apps.agent_runs.execution.workflow_context import AgentRunWorkflowContextMixin
from apps.agent_runs.execution.workflow_state import AgentRunWorkflowStateMixin


class AgentRunWorkflowMemoryMixin(
    AgentRunWorkflowContextMixin,
    AgentRunReportMixin,
    AgentRunWorkflowStateMixin,
):
    pass
