# Plan
1. Add schema migrations for MCP approval mode defaults and per-tool tool-setting records.
2. Wire MCP dashboard tool settings (modal UI, load/save per-tool overrides via API).
3. Add pre-flight required-field validation for tool calls to surface missing inputs before execution.
4. Extend MCP API tests to cover approval defaults, tool-settings endpoint, and cache expiration metadata.

# Business POV
Scenario 1: A support lead connects GitHub MCP and sets default approval to “approve writes.”
- Expected UX: Connection list shows the approval badge immediately; no runtime behavior changes yet, but the admin can document safe defaults.
- Success: The team knows at a glance which connectors require approval and can prepare per-tool overrides.

Scenario 2: A CS agent opens the tool settings modal and marks “create_issue” as write + requires approval.
- Expected UX: Tool list loads from cached schema; toggling a tool updates the UI and persists via Save; no errors or reload needed.
- Success: Per-tool overrides are saved reliably and visible on next open.

Scenario 3: A banker asks the agent to open an account but omits required fields.
- Expected UX: The system blocks the tool call and responds by listing all missing fields in one message.
- Success: No partial tool execution occurs; user provides missing info in one follow-up.

Scenario 4: A stale MCP cache expires.
- Expected UX: Connection card shows “Cache Expired” until the admin re-tests the connection.
- Success: Teams understand why tool lists may be empty and know how to refresh.
