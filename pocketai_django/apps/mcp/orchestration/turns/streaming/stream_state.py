from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _TurnStreamState:
    first_pass_chunks: list[str] = field(default_factory=list)
    answer_chunks: list[str] = field(default_factory=list)
    mode: str = "initial"
    buffer: str = ""
    dropped: list[str] = field(default_factory=list)
    initial_started: bool = False
    final_started: bool = False
    inline_blocks_detected: bool = False
    dsml_skip_line: bool = False
