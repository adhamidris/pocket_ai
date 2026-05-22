from __future__ import annotations

import dataclasses

@dataclasses.dataclass
class KnowledgeToolResult:
    """Structured record of knowledge snippets returned by tool calls."""

    snippets: tuple[dict[str, object], ...] = dataclasses.field(default_factory=tuple)
    source_tool: str | None = None
    note: str | None = None
