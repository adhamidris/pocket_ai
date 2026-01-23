# Plan: Chat Portal Email Draft Actions

## Implementation plan
1. Add dedicated portal endpoints for `send draft` and `discard draft`.
2. Resolve `email_account_id` server-side via `Conversation.metadata.email_pending_draft` when available.
3. Wire the email draft card buttons to:
   - approve/deny tool approvals when an `approval_id` exists
   - otherwise call the new draft action endpoints.
4. Prevent extra LLM “tool_iteration” calls after `email_create_draft` in “send email” flows:
   - if approval is required, pause immediately after the draft is created until approve/reject
   - if approval is not required, auto-send without pausing.
5. Update the draft card UI state after completion (sent / not sent) without removing the card.
6. Fix CSS so `[hidden]` reliably hides the email action rows.

## Business POV (expected UX)
- **Customer-approved send:** The assistant drafts an email during a chat; once the draft is created, the user clicks “Send Email” on the draft card and immediately sees “Email sent ✓” with the card remaining in the transcript for auditability.
- **Customer rejects send:** The user clicks “Don’t Send” and sees “Not sent”; the draft stays visible for context and the system clears any “pending draft” marker so it won’t be accidentally sent in a later step.
- **No surprise reasoning while waiting:** After the draft is created, the portal pauses the workflow and shows only the draft card (no extra “thinking/tool step” blocks) until the user approves or rejects.
- **Auto-approve send:** If policy allows auto-send, the draft card transitions to “Sending email…” then “Email sent ✓” without requiring user interaction.
- **Mixed approval flows:** If the LLM does trigger an `email_send_draft` that requires approval, the existing approval mechanism still works; the draft card does not rely on an approval id that doesn’t exist.
- **Operational resilience:** If the draft id is missing or the send fails (OAuth, provider error), the user gets a clear error and can retry; no silent failures.
- **Tenant + privacy safety:** Draft actions are scoped to the portal session’s conversation and operate under the correct tenant context; no cross-tenant reads/writes.
