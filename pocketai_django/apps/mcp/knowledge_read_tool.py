"""
read_knowledge MCP tool handler and agentic read engine.
"""

from __future__ import annotations

from typing import Mapping

from apps.conversations.models import Conversation

from .knowledge_read.tables import _agentic_table_chunk_snippets
from .knowledge_read.wrapper import _read_knowledge_agentic_wrapper
from .types import ToolExecutionContext




def _read_knowledge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Public read_knowledge entrypoint.

    Enforces the refs-first agentic contract.
    """
    return _read_knowledge_agentic_wrapper(arguments, conversation, context)
