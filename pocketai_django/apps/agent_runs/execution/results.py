from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class AgentRunProcessResult:
    run_id: str
    status: str
    requeued: bool = False
    error: str | None = None
