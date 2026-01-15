# Plan: Portal tool trace + search results panel

## Goal
Show what the assistant did on each turn in the public chat portal UI by default (collapsed): tool calls, searches, returned snippets/files, and reads — inline under the assistant message in an expandable panel.

## Constraints / invariants
- Privacy by default: redact/clip potentially sensitive fields and keep payloads bounded.
- Tenant isolation: only ever show tool activity for the current conversation/tenant.
- Do not persist raw tool outputs into DB by default (avoid bloat and “deleted content” retention).

## Steps
1. Emit a bounded, sanitized tool/search payload on every portal turn (configurable via env).
2. Serialize a bounded, sanitized debug payload from the per-turn tool context.
3. Extend portal JS to render a collapsible panel under each assistant message by default.
4. Document how to disable the feature quickly if a tenant prefers less transparency.

---

# Business POV

## Why this matters
Tool visibility improves end-user trust (“show me where this came from”) and shortens debugging loops for retrieval quality and tool routing — without needing to sift through server logs.

## Scenarios
1. **Internal QA investigating a wrong answer**
   - UX: QA asks a question, expands “Tools” to see which searches ran and which snippets were used.
   - Success: QA can reproduce + report a concrete “search returned X, read Y, answer missed Z” within minutes.

2. **End user trust / transparency**
   - UX: A customer can expand “Tools” and see the exact query the assistant searched and which documents/snippets were used.
   - Success: Higher trust and fewer “hallucination” complaints; better CSAT on knowledge-grounded answers.

3. **Engineer diagnosing RAG regressions across tenants**
   - UX: Engineer reviews tool traces directly in the portal (still tenant-scoped and bounded).
   - Success: Faster root-cause identification (bad ranking, wrong primary document tracking, query rewrite issues) with less log spelunking.

4. **Customer support troubleshooting a tenant’s portal behavior**
   - UX: Support staff uses a privileged/debug-only view to capture a screenshot of tool traces and snippet titles (without exposing raw content).
   - Success: Fewer back-and-forth requests for “what happened” and clearer escalation artifacts for engineering.

5. **Risk scenario: disclosure of sensitive doc labels**
   - UX risk: Visitors may see internal document titles or previews that tenants consider sensitive (even if the answer itself was safe).
   - Mitigation: Aggressive clipping + PII redaction + bounded lists; keep the panel collapsed; provide an env kill-switch.
   - Success: Tenants can opt out quickly while retaining a modern UI pattern where desired.

## Metrics / acceptance signals
- Debug panel appears under each assistant message (collapsed by default).
- Payload size remains bounded (no large snippets / logs).
- No cross-tenant leakage in displayed doc titles/IDs (validated by tenant-scoped conversations).
