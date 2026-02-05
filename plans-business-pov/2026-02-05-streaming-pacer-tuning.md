# Plan: Chat Portal Streaming Pacer Tuning

## Implementation Plan
1. Introduce explicit pacing constants for characters-per-second and per-frame caps that are stable across refresh rates.
2. Replace mode-specific fast-drain behavior with a single paced reveal path for `normal`, `boundary`, and `finalize`.
3. Bound pacer budget carryover to prevent latent burst release after stalls or mode transitions.
4. Keep explicit hard flush behavior only in `_finalizeStreamingTextSegment()` for turn completion.
5. Validate by checking syntax and confirming existing trace fields still report `visibleLen/backlogLen/mode`.

## Business POV

### Scenario 1: Standard answer, no tools
- User experience: text appears steadily and consistently instead of dumping visible chunks.
- Potential regression: total answer completion may be slightly later by a few hundred ms.
- Success signal: lower peak `visibleLen` delta per render and fewer “too fast” reports.

### Scenario 2: Tool boundary appears mid-answer
- User experience: tool cards still respect ordering, but text does not suddenly accelerate before/after the tool card.
- Potential regression: boundary transitions may feel less snappy than current fast-drain.
- Success signal: smoother visual continuity around `block_tool_use` and `block_tool_result` events.

### Scenario 3: Turn persisted / final reconciliation
- User experience: answer remains paced until explicit finalization, then ends cleanly without pre-final bursts.
- Potential regression: if network is unstable, users may briefly see a small backlog before final flush.
- Success signal: no large pre-final render jumps while preserving exact final text after reconcile.

### Scenario 4: High-refresh devices (120Hz+) and tab stalls
- User experience: pacing speed remains similar across devices; no hidden budget causing catch-up dumps after focus changes.
- Potential regression: under severe stalls, stream may prioritize consistency over immediate catch-up.
- Success signal: reduced variance of perceived stream speed between 60Hz and 120Hz sessions.
