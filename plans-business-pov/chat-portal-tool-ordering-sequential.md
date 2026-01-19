# Chat Portal: Sequential Tool Call Ordering (Streaming + Refresh)

## Plan
1. Persist a stable tool insertion position with each tool event (UTF-16 text offset + per-tool sequence index).
2. Fix streaming segmentation to split text at the first tool boundary without “pulling” post-tool text above the tool cards.
3. Rebuild stored messages on page load as ordered text+tool segments using persisted offsets (fallback gracefully for older messages without offsets).
4. Validate via quick syntax checks and manual portal flow.

## Business POV
### Goal
Make MCP/external tool activity feel trustworthy and readable by keeping tool cards exactly where they happened in the assistant’s response — during live streaming and after refresh.

### Scenarios
1. **User watches an answer that streams, then calls tools mid-way**
   - Expected: Text appears, then tool cards appear inline, then the assistant continues below them. No “jumping” text blocks.
2. **User refreshes the page mid-conversation**
   - Expected: The chat reconstructs in the same order as originally seen; tool cards remain between the same text chunks.
3. **Multiple tools run back-to-back**
   - Expected: Tool cards appear consecutively (in call order) without inserting extra text segments between them.
4. **Tool approval required**
   - Expected: The approval prompt/tool card stays anchored at the point it was requested, so the user understands what triggered it.

### Success Metrics
- **User trust**: Fewer “the assistant moved stuff around” complaints; tool actions read as an auditable timeline.
- **Supportability**: Refresh bugs are eliminated because placement is deterministic (offset + sequence), not heuristic.

