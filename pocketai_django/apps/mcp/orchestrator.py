"""
Compatibility bridge for the MCP portal orchestrator service.

The implementation lives in apps.mcp.orchestration.service. Keep this module
small so legacy imports such as `apps.mcp.orchestrator.McpOrchestratorService`
continue to work without making the MCP root directory look like the
implementation home.
"""

from __future__ import annotations

from core.cache_resilience import reserve_counter

from . import tools as mcp_tools
from .orchestration.service import McpOrchestratorService

__all__ = ["McpOrchestratorService", "mcp_tools", "reserve_counter"]
