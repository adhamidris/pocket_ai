# Plan — Chat Portal External Tools UX

## Plan
1. Audit chat portal streaming + tool hooks
2. Emit tool lifecycle stream events
3. Render external tool cards UI
4. Add redaction and truncation
5. Verify tests and UX flows

## Business POV

### Why this matters
When the assistant calls an external tool (e.g., an MCP server), the visitor experiences a “dead moment” unless we surface what’s happening. This creates mistrust (“did it freeze?”) and causes rage-clicking / repeated sends, which increases cost and rate-limit risk.

### Scenarios (expected UX)
1. **Visitor triggers an MCP call (happy path)**  
   - The chat shows an inline “External tool” card (not a spinner) with a running status and subtle progress animation.  
   - When finished, it flips to “Succeeded” with duration; details remain collapsed by default.  
   - Expanding reveals redacted, bounded input/output to improve trust and debuggability.

2. **Tool fails (rate limit / auth / upstream error)**  
   - The card flips to “Failed” with a concise error summary (no silent failure).  
   - Expanding shows redacted input/output and any hint that helps resolve (e.g., “authentication missing” / “retry later”).

3. **Multiple external tool calls in one answer**  
   - Each call gets its own compact card, ordered by time.  
   - Cards remain collapsed by default to keep the chat premium and uncluttered; the answer stays readable.

4. **Mobile user experience**  
   - Cards remain one-line summaries when collapsed; tap to expand shows input/output with horizontal scroll for JSON.

### Success signals
- Fewer repeated “Send/Test” attempts during tool execution (reduced rage clicks).
- Reduced support/debug time (tool call context is visible on-demand).
- Improved perceived responsiveness and trust (users can see progress and outcomes without exposing secrets).

