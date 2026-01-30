# Plan: Sub-agent approvals in main thread (CTAs + preview)

## Plan
1. Persist a safe email-draft preview at `email_create_draft`.
2. Attach the preview to the approval object and the `needs_approval` portal message metadata.
3. Render an approval preview + Approve/Deny CTAs inside the main-thread sub-agent card (only while pending).
4. Cache per-message agent-run metadata in the portal so polling updates can’t “forget” approval IDs/previews.

## Business POV
- Scenario: Customer continues chatting while a sub-agent runs in the background, and an email needs approval.
  - Expected UX: the main thread shows “Awaiting approval” + Approve/Deny + the email preview (To/Subject/Body).
  - Success: approvals happen inline (no forcing a context switch to the Tasks panel), reducing approval latency.
- Scenario: Multiple sub-agents run in parallel and each produces multiple milestones.
  - Expected UX: each milestone card is distinguishable (subtitle + preview when relevant), avoiding “dead/duplicate” looking tasks.
  - Success: fewer mistaken approvals and fewer “which task is this?” questions.
- Scenario: User approves/denies, then the run continues or cancels.
  - Expected UX: CTAs disappear, the card shows a small “Approved”/“Denied” pill, and the run proceeds without re-requesting approval.
  - Success: no duplicate prompts, and the user can audit what was approved later.
- Scenario: Multi-tenant/privacy constraints.
  - Expected UX: previews are clipped and stored only where needed (approval + conversation message), not in generic run/event logs.
  - Success: managers can view run logs without exposing full email contents/PII by default.

