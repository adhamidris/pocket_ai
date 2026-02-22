# Phase 0 Baseline: MCQ Scope Clarification

Date: 2026-02-22  
Feature Flag: `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED`

## Scope

Phase 0 captures baseline behavior for MCQ clarification before optimization, and defines measurable acceptance targets for subsequent phases.

All enhancements in later phases must remain fully gated by:

- `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=true` -> enhanced MCQ behavior active
- `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=false` -> existing non-MCQ clarification behavior preserved

## Data Sources

- `conversations_portal_turn`
- `conversations_portal_turn_event` (`type=turn_persisted`, `payload.debug_tools.tool_trace`)
- `conversations_portal_turn_event` (`type=status`, for `answer_finalized` timing)

## Reproducibility

Run from `pocketai_django/`:

```bash
.venv/bin/python manage.py shell -c "..."
```

Recommended extraction pattern:

- Pull `turn_persisted` payload
- Read `payload.debug_tools.tool_trace`
- Count tool calls and `invalid_id` errors from `llm_response.content_json.errors`
- Pull `status` events and compute `answer_finalized - turn.created_at`

Baseline run analyzed in detail:

- Turn/Run ID: `90d09724-40d8-4d1e-88da-31ec7775f9ce`

Recent sample analyzed for context:

- Window: last 14 days
- MCQ candidate turns: 18

## Metric Definitions

1. `click_to_answer_ms`
- `answer_finalized_status_timestamp - turn.created_at` (milliseconds)

2. `tool_calls_after_click`
- Count of `debug_tools.tool_trace` entries in the click follow-up turn

3. `read_invalid_id_count`
- Count of `read_knowledge` errors where `error_code == "invalid_id"`

4. `loop_rate`
- Turn-level boolean loop signal:
- `search_knowledge_calls >= 3`
- `search_knowledge(needs_clarification) >= 2`
- and (`read_invalid_id_count > 0` or `read_knowledge_calls > 1`)
- Report as share of affected MCQ turns

## Baseline Results

### A) Detailed baseline (problem run)

For `90d09724-40d8-4d1e-88da-31ec7775f9ce`:

- `click_to_answer_ms`: `43,872`
- `tool_calls_after_click`: `9`
- `search_knowledge_calls`: `5`
- `search_knowledge_needs_clarification`: `4`
- `read_knowledge_calls`: `3`
- `read_invalid_id_count`: `4`
- `loop_detected`: `true`

Observed failure signature:

- `read_knowledge` received category labels (non-UUID) as ref ids
- repeated `search_knowledge` clarification retries
- search budget exhaustion path appears before final fallback answer

### B) Recent context baseline (14-day MCQ candidates)

- MCQ candidate turns: `18`
- `click_to_answer_ms`:
- `p50=7,649`
- `p95=15,447`
- average tool calls per turn: `2.06`
- turns with any `invalid_id`: `5.6%` (1/18)
- `invalid_id` average per turn: `0.222`
- loop-turn rate (definition above): `5.6%` (1/18)

Note:

- The outlier run above is the only severe loop in the sampled set and dominates the reliability risk for click follow-up flow.

## Phase 0 Acceptance Targets (for Phase 8 verification)

Targets are for MCQ click follow-up turns when `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=true`.

1. Latency
- `click_to_answer_ms p95 <= 10,000`
- `click_to_answer_ms p50 <= 6,000`

2. Tool efficiency
- average `tool_calls_after_click <= 2.0`
- `tool_calls_after_click` hard cap in common path: `<= 3`

3. Correctness
- `read_invalid_id_count == 0`
- `% turns with invalid_id == 0%`

4. Loop containment
- `loop_rate <= 1%`
- `% turns hitting search budget exceeded after click <= 0.5%`

## Operational Rule for Next Phases

No additional feature flags should be introduced for this enhancement stream.

Nested behavior (payload unification, category-key mapping, click auto-read, invalid-id repair, fallback sequencing) must remain controlled by the existing single flag:

- `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED`
