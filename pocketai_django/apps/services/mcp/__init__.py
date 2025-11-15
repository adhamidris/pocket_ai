"""
MCP orchestration package.

This module tree hosts the upcoming tool-calling orchestrator and its helpers.
Everything here should remain isolated from the legacy AiOrchestratorService so
we can evolve both systems independently.
"""

from .orchestrator import McpOrchestratorService

__all__ = ["McpOrchestratorService"]
