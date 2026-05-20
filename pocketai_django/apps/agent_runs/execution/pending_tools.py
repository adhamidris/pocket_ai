from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Mapping

from django.conf import settings

from apps.agent_runs.models import AgentRun, AgentRunEventStream, AgentRunEventType
from apps.rag.rag_logging import structured_log


logger = logging.getLogger(__name__)


class AgentRunPendingToolMixin:

    def _execute_pending_tool_call(
        self,
        *,
        run: AgentRun,
        pending_tool_call: Mapping[str, object],
        execution_conversation: Any,
        orchestrator: Any,
        on_tool_event: Any,
    ) -> bool:
        """
        Execute a pending tool call that was approved.

        Returns True if tool was executed successfully, False otherwise.
        """
        from apps.conversations.content_blocks import make_tool_use_block, make_tool_result_block
        from apps.conversations.models import ConversationMessage, ConversationSender
        from apps.mcp import tools as mcp_tools
        from apps.mcp.types import ToolExecutionContext

        tool_name = str(pending_tool_call.get("tool_name") or "").strip()
        tool_call_id = str(pending_tool_call.get("tool_call_id") or "").strip()
        arguments = pending_tool_call.get("arguments") or {}
        connection_id = pending_tool_call.get("connection_id")
        remote_tool_name = str(pending_tool_call.get("remote_tool_name") or "").strip()
        event_id = str(pending_tool_call.get("event_id") or f"evt_{uuid.uuid4().hex[:12]}")

        if not tool_name:
            logger.warning("pending_tool_call missing tool_name, skipping execution")
            return False

        started = time.monotonic()
        tool_result = None
        error_message = None

        try:
            # Determine if this is an MCP remote tool or built-in tool
            if connection_id and remote_tool_name:
                # MCP remote tool - use orchestrator's remote execution
                from apps.mcp.models import McpConnection

                connection = McpConnection.objects.filter(id=connection_id).first()
                if connection:
                    tool_result = orchestrator._execute_remote_mcp_tool(
                        connection=connection,
                        tool_name=tool_name,
                        remote_tool_name=remote_tool_name,
                        arguments=arguments,
                        conversation=execution_conversation,
                        tool_event_id=event_id,
                        on_tool_event=on_tool_event,
                    )
                else:
                    error_message = f"MCP connection {connection_id} not found"
            else:
                # Built-in tool (email tools, etc.)
                tool_context = ToolExecutionContext()
                tool_result = mcp_tools.execute_tool(
                    tool_name,
                    dict(arguments) if isinstance(arguments, Mapping) else {},
                    conversation=execution_conversation,
                    context=tool_context,
                )
        except Exception as exc:
            logger.exception("pending_tool_call execution failed tool=%s run=%s", tool_name, run.id)
            error_message = str(exc)

        duration_ms = int((time.monotonic() - started) * 1000)

        # Build the result
        if tool_result is None:
            tool_result = {
                "tool": tool_name,
                "status": "error",
                "error": error_message or "Tool execution failed",
            }

        status = str(tool_result.get("status") or "ok").strip()

        # Emit tool event for UI
        if on_tool_event:
            try:
                on_tool_event({
                    "event_id": event_id,
                    "phase": "finished",
                    "status": status,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "duration_ms": duration_ms,
                    "input": dict(arguments) if isinstance(arguments, Mapping) else {},
                    "output": tool_result,
                })
            except Exception:
                logger.exception("on_tool_event callback failed")

        # Inject tool_use + tool_result into execution conversation
        remote_info = None
        if connection_id and remote_tool_name:
            remote_info = {
                "connection_id": str(connection_id),
                "remote_tool": remote_tool_name,
            }

        tool_use_block = make_tool_use_block(
            event_id=event_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=dict(arguments) if isinstance(arguments, Mapping) else {},
            status=status,
            duration_ms=duration_ms,
            remote=remote_info,
        )

        tool_result_block = make_tool_result_block(
            event_id=event_id,
            tool_name=tool_name,
            output=tool_result,
            status=status,
            duration_ms=duration_ms,
        )

        # Create the message with both blocks
        ConversationMessage.objects.create(
            conversation=execution_conversation,
            sender=ConversationSender.AI,
            body=f"Executed approved tool: {tool_name}",
            content_blocks=[tool_use_block, tool_result_block],
            metadata={
                "source": "agent_run",
                "agent_run_id": str(run.id),
                "type": "pending_tool_execution",
                "tool_name": tool_name,
            },
        )

        # Log the event
        self._append_event(
            run,
            stream=AgentRunEventStream.EXECUTED,
            event_type=AgentRunEventType.PROGRESS,
            label=f"Executed approved tool: {tool_name}",
            payload={
                "tool_name": tool_name,
                "status": status,
                "duration_ms": duration_ms,
                "was_pending_approval": True,
            },
        )

        try:
            warn_ms = int(getattr(settings, "MCP_SLO_APPROVAL_TOOL_EXECUTION_WARN_MS", 5000) or 0)
            slow = bool(warn_ms and duration_ms >= warn_ms)
            structured_log(
                "mcp",
                "approval.pending_tool_execution",
                {
                    "tool_name": tool_name,
                    "remote_tool_name": remote_tool_name,
                    "is_remote": bool(connection_id and remote_tool_name),
                    "status": status,
                    "success": status.lower() not in {"error", "failed"},
                    "duration_ms": duration_ms,
                    "error_code": tool_result.get("error_code") if isinstance(tool_result, Mapping) else None,
                    "slo": "slow" if slow else None,
                    "slo_warn_ms": warn_ms if slow else None,
                },
                context={
                    "business": getattr(run, "business_profile_id", None),
                    "run": getattr(run, "id", None),
                    "conversation": getattr(execution_conversation, "id", None),
                },
                level=logging.WARNING if slow else logging.INFO,
            )
        except Exception:  # pragma: no cover - observability must not break resume flow
            pass

        return True
