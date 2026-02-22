# Phase 8 Rollout: MCQ Scope Clarification

Date: 2026-02-22  
Feature Flag: `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED`

## Rollout Rule

This enhancement remains a single feature surface:

- `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=true`: full MCQ flow active (category key selection, mapped refs auto-read, invalid-id guardrails, scoped fallback sequencing).
- `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=false`: existing non-MCQ clarification behavior only.

No additional rollout flags are required.

## Deploy Sequence

1. Deploy backend changes to staging with `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=true` for the staging tenant cohort.
2. Run smoke prompts:
- `tell me about plus fees`
- click one category chip
- verify direct answer path (no repeated search loops)
3. Validate logs for expected path markers:
- `search.scope_resolution`
- `search.scope_auto_read` OR `search.scope_fallback`
- no `read_knowledge.invalid_refs stage=retry_limit_blocked`
4. Promote to production using normal deploy flow.
5. Keep production rollback ready: set `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=false` if KPI regressions exceed thresholds.

## KPI Targets (from Phase 0)

Track MCQ click follow-up turns only:

1. `click_to_answer_ms`
- p50 `<= 6,000`
- p95 `<= 10,000`

2. `tool_calls_after_click`
- average `<= 2.0`
- common-path hard cap `<= 3`

3. Invalid read IDs
- `read_invalid_id_count == 0`
- `% turns with invalid_id == 0%`

4. Loop containment
- `loop_rate <= 1%`
- `% turns with search budget exceeded after click <= 0.5%`

## Monitoring Queries

### A) Fast log checks (runtime)

```bash
# MCQ click resolution events
rg "stage=scope_resolution" var/logs/rag.log | tail -n 50

# Direct auto-read success path
rg "stage=scope_auto_read" var/logs/rag.log | tail -n 50

# Scoped fallback telemetry (new in phase 8)
rg "stage=scope_fallback" var/logs/rag.log | tail -n 100

# Invalid read ref guardrail telemetry (new in phase 8)
rg "stage=invalid_refs" var/logs/rag.log | tail -n 50

# Blocked invalid ref retry loops (must stay near zero)
rg "invalid_ref_retry_limit|retry_limit_blocked" var/logs/rag.log | tail -n 50

# Overall slow-turn check for click follow-ups
rg "stage=turn.summary" var/logs/rag.log | rg "slo=slow" | tail -n 50
```

### B) Structured event fields to trend

Use these event families for dashboard metrics:

- `mcp.trace stage=search.scope_resolution`
- `mcp.trace stage=search.scope_auto_read`
- `mcp.trace stage=search.scope_fallback`
- `mcp.trace stage=read_knowledge.invalid_refs`
- `mcp.trace stage=turn.summary`
- `mcp.trace stage=turn.metrics`

Recommended dimensions:

- `business`
- `conversation`
- `status`
- `scope_fallback_reason`
- `scope_fallback_read_status`
- `scope_auto_read_status`

## Alert Conditions

Trigger investigation when any of the below is true for a 1-hour window:

1. `read_knowledge.invalid_refs` count > 0 on MCQ click follow-up turns.
2. `search.scope_fallback stage=source_followup_blocked_repeat` spikes above baseline.
3. `search_budget_exceeded` appears in MCQ click follow-up turns.
4. p95 `turn.summary.duration_ms` for MCQ click follow-up turns exceeds `10,000` ms.

## Triage Runbook

1. Confirm the selected path per failing turn:
- `scope_auto_read` expected when mapped refs are strong.
- `scope_fallback` expected when mapping is missing/stale/low confidence.
2. If `invalid_refs` appears:
- inspect `scope_resolution.selection_source` and mapped refs state.
- confirm UUID-only refs reached `read_knowledge`.
3. If repeated source follow-up appears:
- verify ambiguity reason is `auto_source_ambiguity` and only one follow-up is emitted per turn.
4. If latency regresses:
- compare share of `scope_fallback` vs `scope_auto_read` paths.
- verify read budgets and search budgets are not exhausted in common path.

## Exit Criteria

Phase 8 is complete when:

1. Production canary and full traffic remain stable for at least 7 days.
2. KPI targets above are met.
3. Error profile remains stable with no MCQ click-path `invalid_id` regressions.
