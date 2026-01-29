# Sub-Agents: Background Runs, Automations, Watchers (V1)

## Plan
1. Define the **core primitives + contracts** (backend + UI):
   - `RunSpec`: goal, success criteria, tool allowlist, constraints, output schema, approval requirements, visibility.
   - `AgentRun`: one execution of a `RunSpec` (one-off runs + automation-triggered runs).
   - `AgentRunEvent` (append-only): `progress`, `needs_user`, `needs_approval`, `result`, `error`, `paused`, `canceled`.
   - `AgentRunArtifact`: links to generated files/records.
   - `AgentRunMemoryItem`: structured memory (facts / SOP steps / decisions), never raw dumps by default.
   - **Truthfulness invariant**: UI separates *Plan* (what it intended) vs *Executed log* (what actually happened).
2. Implement tenant-safe persistence + APIs:
   - Create/list/get runs for an agent + business profile.
   - Create/list automations (cron/manual/webhook triggers) that spawn runs.
   - Run approvals + user-input endpoints (resume/deny/cancel).
   - Strict tenant scoping on every query (`business_profile_id`).
3. Implement background execution runner:
   - Worker loop that claims queued runs and executes them with a bounded budget.
   - Runs execute in an **isolated execution context** (fresh run conversation) with safe default tools:
     - Default allowlist = everything **minus orchestration tools** (no nested `create_agent_run`).
     - File/PDF tools transparently operate on the anchor chat’s uploaded files so delegated tasks still “see” attachments.
   - Tool gating from: tenant policy + user policy + per-tool approval mode + integration ownership.
   - Idempotency keys + retries for safe tool calls; deterministic backoff.
4. Streaming + UI integration (3-panel portal):
   - Extend SSE to stream run events into the right “Tasks” panel (pills/chips per run).
   - Collapsed view shows current step + state; expanded view shows plan + executed log + artifacts + CTAs.
   - CTAs: approve/deny, answer questions, pause/resume/cancel, open artifact.
5. Approval + user-input loops (human-in-the-loop):
   - `needs_approval`: orchestrator prompts in chat + task CTA; approval resumes the run.
   - `needs_user`: orchestrator asks crisp questions; user reply resumes the run.
   - Store decisions as structured memory (decision + timestamp + actor).
6. Agent-to-agent inbox (single-user compatible, team-ready later):
   - Introduce `AgentRequest`: Agent A → Agent B with structured question + context references (no raw dumps).
   - Separate inbox UI with states: `open → in_progress → resolved`.
   - Tasks panel shows a minimal log line: “Agent A → Agent B: request subject…”.
7. Automations/triggers (V1 scope):
   - Cron schedules (e.g., “every day at 8am”) + “Run now”.
   - Prefer webhooks where available; polling as fallback.
   - Each trigger execution creates a new `AgentRun` and emits run events to UI.
8. Watchers (V1 scope):
   - Start with polling-based watchers (email/events) with dedupe + rate limits.
   - Upgrade to provider push/webhooks as integrations mature.
9. Output destinations (default + configurable):
   - Default for automations/watchers: write results to a **dedicated Automation thread** per automation + show live progress in Tasks panel.
   - Optional: post a short summary into the current chat thread (when user explicitly asks).
   - Optional: notifications (email/push/Slack) with strict privacy controls.
10. Auditability + manager visibility:
   - Every executed tool action becomes an immutable “executed log” entry (redacted inputs).
   - Managers (or configured roles) can view run status and executed actions without exposing raw sensitive payloads.
11. Rollout + safety:
   - Feature flag per business profile (sub-agents on/off).
   - Safe defaults: queue instead of hard-fail when limits are hit; show “queued because…” in UI.
   - Keep single-agent mode working as fallback.

### Deferred / Pending
- Teams, hierarchy, and multi-user workspace permissions are **pending** (see `plans-business-pov/teams-hierarchy-permissions-pending.md`).

## Business POV
### Goal
Turn the chat portal into an always-on **operator console**: the orchestrator stays conversational while background sub-agents reliably execute work, request approvals, and log what actually happened.

### Scenarios
1. **Daily sales summary automation (solo user)**
   - User: “Every day at 8am, summarize yesterday’s sales and email me a report.”
   - Expected: the automation runs silently, shows progress in Tasks panel, writes the final report to its Automation thread, and sends a notification.
2. **Email watcher + approval**
   - Watcher detects an urgent client email and drafts a reply.
   - Expected: run pauses at `needs_approval`; user approves in the task CTA; the email is sent; the executed log shows the exact send action.
3. **One-off deep task while user keeps chatting**
   - User: “Prepare a proposal draft and a 1-slide executive summary.”
   - Expected: orchestrator keeps the conversation flowing; sub-agent runs in the background; artifacts appear as downloadable outputs; executed log remains truthful.
4. **Manager review**
   - Manager inspects what happened after an automation changed something in an external system.
   - Expected: manager can see a clean timeline (what was planned vs what was executed), approvals granted, and which user-owned integration was used.

### Success Metrics
- Increased % of sessions that end with completed “runs” (vs chat-only).
- Reduced time-to-completion for multi-step operational tasks.
- High approval completion rate with low “approval fatigue”.
- Fewer support tickets about “agent claimed it did X” (trust / audit).
