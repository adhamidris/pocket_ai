# Teams, Hierarchy, Permissions (Pending)

Status: **Pending / Deferred** (design + scoping captured here so we can ship sub-agents V1 first).

## Plan
1. Define the workspace model:
   - `Workspace` (or reuse `BusinessProfile` as workspace) + `WorkspaceMember` (user + role).
   - Roles: owner/admin/manager/member (minimum), extendable later.
2. Map “virtual assistants” to real humans:
   - `AgentProfile` remains the orchestrator persona.
   - Add optional assignment: `AgentProfile.assigned_user` (or join model) to represent “this agent is operated by employee X”.
3. Permission framework (role-based + overrides):
   - Per-tool allowlist/denylist at workspace + role + user + agent levels.
   - Approval policy per tool/action/destination (e.g., send email only to allowed domains).
4. OAuth + integration boundaries:
   - OAuth remains per-user.
   - Enforce: agent actions only use the integration credentials of the assigned/initiating user unless explicitly delegated by policy.
5. Extend agent-to-agent inbox to multi-user:
   - Reuse `AgentRequest` (introduced in sub-agents V1) and route requests to the **assigned user** when applicable.
   - Keep the separate inbox UI + audit trail, but enforce role-based visibility for managers/members.
   - Preserve the minimal Tasks-panel logging line: “Agent A → Agent B: request subject…”.
6. Cross-agent collaboration rules:
   - Allow A to request: (a) answer from B’s memory, (b) B runs sub-agents, (c) B asks its human.
   - Enforce visibility: B decides what to disclose; raw sensitive payloads never auto-shared.
7. Invite + onboarding:
   - Owner creates hierarchy/roles, sends invite emails, members join workspace, are assigned agents.
8. Manager oversight:
   - Configurable visibility into runs, requests, approvals, and executed logs.

## Business POV
### Goal
Let a business owner run a company-like operating model: employees + their orchestrators collaborate safely, with clear boundaries and accountability.

### Scenarios
1. **Owner invites finance lead**
   - Finance agent is assigned to a real employee; actions run under that employee’s OAuth.
2. **Marketing agent requests finance input**
   - Marketing agent sends an inbox request to finance agent; finance agent replies or asks its human; request is fully logged.
3. **Manager audits a sensitive action**
   - Manager sees executed logs and approvals but doesn’t automatically see raw payloads unless permitted.

### Success Metrics
- Team adoption (weekly active members per workspace).
- Reduced internal back-and-forth time (requests resolved faster than email/Slack).
- Fewer permission incidents (attempted unauthorized actions blocked + explained).
