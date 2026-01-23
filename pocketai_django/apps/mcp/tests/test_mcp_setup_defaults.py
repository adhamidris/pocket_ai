from __future__ import annotations

from django.test import SimpleTestCase

from apps.mcp.orchestrator import McpOrchestratorService
from apps.mcp.types import ToolExecutionContext
from apps.mcp import tools as mcp_tools


class McpSetupDefaultsTests(SimpleTestCase):
    def test_apply_setup_defaults_merges_missing_schema_keys_only(self) -> None:
        class FakeConnection:
            credentials = {"setup_fields": {"connection_string": "postgresql://user:pass@host:5432/db", "unused": "x"}}

        input_schema = {
            "type": "object",
            "properties": {
                "connection_string": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["connection_string", "query"],
        }

        service = McpOrchestratorService.__new__(McpOrchestratorService)
        effective, applied = service._apply_mcp_setup_defaults(
            {"query": "select 1"},
            connection=FakeConnection(),
            input_schema=input_schema,
        )
        self.assertEqual(effective["query"], "select 1")
        self.assertEqual(effective["connection_string"], "postgresql://user:pass@host:5432/db")
        self.assertIn("connection_string", applied)
        self.assertNotIn("unused", effective)

    def test_mcp_search_tools_hides_required_args_satisfied_by_defaults(self) -> None:
        context = ToolExecutionContext()
        context.mcp_gateway_catalog = {
            "tool_1": {
                "connection_id": "conn_1",
                "connection_name": "Postgres",
                "remote_tool": "query_db",
                "description": "Run a SQL query",
                "default_arg_keys": ["connection_string"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "connection_string": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["connection_string", "query"],
                },
            }
        }

        result = mcp_tools._mcp_search_tools_handler({"query": "query", "limit": 5}, conversation=None, context=context)  # type: ignore[arg-type]
        self.assertEqual(result.get("status"), "ok")
        results = result.get("results") or []
        self.assertTrue(results)
        required_args = results[0].get("required_args") or []
        self.assertTrue(all(item.get("name") != "connection_string" for item in required_args))

