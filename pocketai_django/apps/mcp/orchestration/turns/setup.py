from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from django.conf import settings

from apps.conversations.models import Conversation
from core.otel import otel_trace

from ... import prompts, tools as mcp_tools
from ...connectors import list_enabled_mcp_connections_for_agent, list_remote_tool_descriptors
from ...runtime.portal_block_stream import PORTAL_BLOCK_TOOL_NAME
from ...types import ToolExecutionContext

TRACER = otel_trace.get_tracer(__name__)


@dataclass(frozen=True)
class _TurnSetup:
    all_remote_connections: Sequence[object]
    messages: Sequence[Mapping[str, object]]
    filter_level: str
    initial_stream_filter_level: str
    tool_context: ToolExecutionContext


@dataclass(frozen=True)
class _TurnToolCatalog:
    disable_tools_for_turn: bool
    gateway_enabled: bool
    native_tool_names_all: frozenset[str]


@dataclass(frozen=True)
class _InitialToolPlan:
    verification_enabled: bool
    verification_blocks_streaming: bool
    streaming_allowed: bool
    tool_definitions_for_model: Sequence[Mapping[str, object]]
    portal_only_tools: Sequence[Mapping[str, object]]
    initial_tools: Sequence[Mapping[str, object]] | None


class McpTurnSetupMixin:

    def _prepare_turn_setup(
        self,
        *,
        conversation: Conversation,
        user_message: str,
    ) -> _TurnSetup:
        with TRACER.start_as_current_span("portal.mcp.turn_setup") as setup_span:
            if setup_span.is_recording():
                setup_span.set_attribute("conversation.id", str(conversation.id))
                setup_span.set_attribute("business.id", str(conversation.business_profile_id))
            all_remote_connections = list_enabled_mcp_connections_for_agent(self.agent)
            has_mcp_connections = bool(all_remote_connections)
            model_id = getattr(self.provider, "model", None) if self.provider else None
            messages = prompts.build_messages(
                conversation=conversation,
                user_message=user_message,
                model_id=model_id,
                has_mcp_connections=has_mcp_connections,
            )
            filter_level = self._filter_level_for_conversation(conversation)
            char_turn_limit = self._char_budget_per_turn(conversation.business_profile)
            char_minute_limit = self._char_budget_per_minute(conversation.business_profile)
            minute_reserver = self._build_minute_budget_reserver(conversation.business_profile, char_minute_limit)
            tool_context = ToolExecutionContext(
                max_chunk_reads_per_turn=self.max_chunk_reads_per_turn,
                max_chunk_pages_per_turn=self.max_chunk_pages_per_turn,
                char_budget_per_turn=char_turn_limit,
                char_budget_per_minute=char_minute_limit,
                minute_budget_reserver=minute_reserver,
                latest_user_message=user_message,
            )
            self._hydrate_seen_items(conversation, tool_context)
            return _TurnSetup(
                all_remote_connections=all_remote_connections,
                messages=messages,
                filter_level=filter_level,
                initial_stream_filter_level=filter_level,
                tool_context=tool_context,
            )

    def _prepare_turn_tool_catalog(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        allowed_tools: set[str] | None,
        wait_for_tool_approval: bool,
        portal_emit_blocks_enabled: bool,
        all_remote_connections: Sequence[object],
        tool_context: ToolExecutionContext,
        feature_state,
    ) -> _TurnToolCatalog:
        new_contract_enabled = bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
        rag_agentic_enabled = bool(getattr(feature_state, "rag_agentic_mode", False)) and new_contract_enabled
        disable_tools_for_turn = self._is_low_intent_message(user_message)

        normalized_tool_allowlist: set[str] | None = None
        if allowed_tools is not None:
            normalized_tool_allowlist = {str(value).strip() for value in allowed_tools if str(value or "").strip()}

        native_registry = mcp_tools.get_native_integration_tool_registry()
        native_tool_names_all = set(native_registry.keys())
        available_native_tool_names = self._available_native_integration_tool_names(
            conversation=conversation,
            registry=native_registry,
        )
        email_registry = mcp_tools.EMAIL_INTEGRATION_TOOL_REGISTRY
        email_tool_names_all = mcp_tools.get_email_integration_tool_names()
        available_email_tool_names = self._available_email_integration_tool_names(
            conversation=conversation,
            registry=email_registry,
        )

        internal_tool_defs: list[Mapping[str, object]] = list(mcp_tools.get_tool_definitions())
        gateway_enabled = bool(all_remote_connections)
        if gateway_enabled:
            internal_tool_defs.extend(mcp_tools.GATEWAY_TOOL_DEFINITIONS)
        if not portal_emit_blocks_enabled:
            internal_tool_defs = [
                tool_def
                for tool_def in internal_tool_defs
                if self._tool_schema_name(tool_def) != PORTAL_BLOCK_TOOL_NAME
            ]
        enable_user_input_tool = not wait_for_tool_approval
        convo_meta = getattr(conversation, "metadata", None)
        convo_meta_map = convo_meta if isinstance(convo_meta, Mapping) else {}
        convo_source = str(convo_meta_map.get("source") or "").strip().lower()
        is_agent_run_conversation = convo_source == "agent_run"
        if is_agent_run_conversation:
            enable_user_input_tool = True
        if not enable_user_input_tool:
            internal_tool_defs = [
                tool_def
                for tool_def in internal_tool_defs
                if self._tool_schema_name(tool_def) != "request_user_input"
            ]
        if rag_agentic_enabled:
            allowed = {
                "search_knowledge",
                "read_knowledge",
                "search_conversation_files",
                "read_conversation_file",
                "pdf_generate",
                "pdf_merge",
                "pdf_extract_pages",
                "pdf_extract_text",
                "request_user_input",
                "start_agent_run",
                "list_agent_runs",
                "get_agent_run",
                "continue_agent_run",
                "list_tasks",
                "draft_task",
                "update_task",
                "request_task_activation",
                "pause_task",
                "search_memory",
                "save_memory",
                "forget_memory",
                PORTAL_BLOCK_TOOL_NAME,
                "initiate_phone_call",
            }
            if gateway_enabled:
                allowed.update({"mcp_search_tools", "mcp_call_tool"})
            allowed.update(available_native_tool_names)
            allowed.update(available_email_tool_names)
            internal_tool_defs = [
                tool_def for tool_def in internal_tool_defs if self._tool_schema_name(tool_def) in allowed
            ]

        internal_tool_defs = [
            tool_def
            for tool_def in internal_tool_defs
            if (
                self._tool_schema_name(tool_def) not in native_tool_names_all
                or self._tool_schema_name(tool_def) in available_native_tool_names
            )
        ]
        internal_tool_defs = [
            tool_def
            for tool_def in internal_tool_defs
            if (
                self._tool_schema_name(tool_def) not in email_tool_names_all
                or self._tool_schema_name(tool_def) in available_email_tool_names
            )
        ]

        if normalized_tool_allowlist is not None:
            internal_tool_defs = [
                tool_def for tool_def in internal_tool_defs if self._tool_schema_name(tool_def) in normalized_tool_allowlist
            ]

        if is_agent_run_conversation:
            forbidden = {
                "start_agent_run",
                "continue_agent_run",
                "list_agent_runs",
                "get_agent_run",
                "draft_task",
                "update_task",
                "request_task_activation",
                "pause_task",
            }
            if normalized_tool_allowlist is not None:
                normalized_tool_allowlist -= forbidden
            internal_tool_defs = [
                tool_def for tool_def in internal_tool_defs if self._tool_schema_name(tool_def) not in forbidden
            ]

        remote_tool_defs: list[dict[str, object]] = []
        self._remote_tool_registry = {}
        if not disable_tools_for_turn:
            remote_descriptors = list_remote_tool_descriptors(all_remote_connections)
            if normalized_tool_allowlist is not None:
                remote_descriptors = [desc for desc in remote_descriptors if desc.safe_name in normalized_tool_allowlist]
            gateway_catalog: dict[str, dict[str, object]] = {}
            remote_registry: dict[str, tuple[object, str]] = {}
            default_arg_keys_by_connection: dict[str, list[str]] = {}
            for desc in remote_descriptors:
                tool_id = desc.safe_name
                remote_registry[tool_id] = (desc.connection, desc.remote_name)
                connection_id = str(getattr(desc.connection, "id", "") or "")
                default_arg_keys = default_arg_keys_by_connection.get(connection_id)
                if default_arg_keys is None:
                    creds = getattr(desc.connection, "credentials", None) or {}
                    setup_fields = creds.get("setup_fields") if isinstance(creds, Mapping) else None
                    if isinstance(setup_fields, Mapping):
                        default_arg_keys = sorted(
                            {
                                str(key).strip()
                                for key, value in setup_fields.items()
                                if str(key).strip() and isinstance(value, str) and value.strip()
                            }
                        )
                    else:
                        default_arg_keys = []
                    default_arg_keys_by_connection[connection_id] = default_arg_keys
                gateway_catalog[tool_id] = {
                    "connection_id": connection_id,
                    "connection_name": str(getattr(desc.connection, "name", "") or ""),
                    "remote_tool": desc.remote_name,
                    "description": desc.description,
                    "input_schema": dict(desc.input_schema) if isinstance(desc.input_schema, Mapping) else None,
                    "default_arg_keys": list(default_arg_keys),
                }
            tool_context.mcp_gateway_catalog = gateway_catalog
            self._remote_tool_registry = remote_registry

        if remote_tool_defs:
            self.tool_definitions = tuple([*internal_tool_defs, *remote_tool_defs])
        else:
            self.tool_definitions = tuple(internal_tool_defs)

        return _TurnToolCatalog(
            disable_tools_for_turn=disable_tools_for_turn,
            gateway_enabled=gateway_enabled,
            native_tool_names_all=frozenset(native_tool_names_all),
        )

    def _prepare_initial_tool_plan(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        transcript: Sequence[Mapping[str, object]],
        tool_context: ToolExecutionContext,
        disable_tools_for_turn: bool,
        on_block_event: Callable[[Mapping[str, object]], None] | None,
    ) -> _InitialToolPlan:
        preplan_enabled = (not disable_tools_for_turn) and self._preplan_enabled_for_business(conversation.business_profile)
        verification_enabled = self._verification_enabled_for_business(conversation.business_profile)
        verification_blocks_streaming = verification_enabled and self._verification_blocks_streaming_for_business(
            conversation.business_profile
        )
        streaming_allowed = not verification_blocks_streaming

        provider_name = (os.getenv("MCP_PROVIDER") or "").strip().lower()
        tool_definitions_for_model = self.tool_definitions
        if provider_name == "deepseek":
            tool_definitions_for_model = tuple(
                tool_def
                for tool_def in tool_definitions_for_model
                if self._tool_schema_name(tool_def) != PORTAL_BLOCK_TOOL_NAME
            )
            self.tool_definitions = tool_definitions_for_model

        portal_only_tools = [
            tool_def for tool_def in tool_definitions_for_model if self._tool_schema_name(tool_def) == PORTAL_BLOCK_TOOL_NAME
        ]
        if not portal_only_tools:
            portal_only_tools = []
        initial_tools: Sequence[Mapping[str, object]] | None = None if disable_tools_for_turn else tool_definitions_for_model
        if disable_tools_for_turn and portal_only_tools and on_block_event:
            initial_tools = portal_only_tools

        if preplan_enabled:
            recent_history = [
                entry for entry in transcript if entry.get("role") in {"user", "assistant"}
            ]
            preplan_message = self._run_preplan(
                conversation=conversation,
                user_message=user_message,
                recent_history=recent_history[-6:],
                tool_context=tool_context,
            )
            preplan_payload: dict[str, object] | None = None
            if preplan_message:
                preplan_payload = self._parse_preplan_payload(preplan_message)
            if preplan_payload:
                tool_context.preplan = dict(preplan_payload)
                planned_tools = preplan_payload.get("tools") or []
                tool_list = [t for t in planned_tools if isinstance(t, str) and t.strip()]
                if set(tool_list) == {"search_knowledge"}:
                    initial_tools = self._include_tool_schemas({"search_knowledge"})

        return _InitialToolPlan(
            verification_enabled=verification_enabled,
            verification_blocks_streaming=verification_blocks_streaming,
            streaming_allowed=streaming_allowed,
            tool_definitions_for_model=tool_definitions_for_model,
            portal_only_tools=portal_only_tools,
            initial_tools=initial_tools,
        )
