# MCP Grouped Retrieval Observability

This runbook tracks the new text-chunk grouping flow (`document_anchor` + grouped window reads).

## Dashboard template

- Ready-to-import Datadog dashboard JSON:
  - `docs/ops/datadog_mcp_grouped_retrieval_dashboard.json`
- Import path in Datadog:
  - **Dashboards → New Dashboard → Import Dashboard JSON**
  - Paste file contents and save.

## Log events

- `search.agentic_conversion`
  - `text_grouping_enabled`
  - `text_group_candidates`
  - `text_group_groups`
  - `text_group_promoted_refs`
  - `document_anchor_refs`
  - `text_group_manifests_cached`
- `read_knowledge.agentic_v2`
  - `text_group_manifest_lookups`
  - `text_group_manifest_hits`
  - `text_group_manifest_misses`
  - `text_group_manifest_hit_rate`
  - `text_group_window_reads`
  - `text_group_chunk_refs_covered`
  - `text_group_upload_refs_covered`

## Suggested dashboard panels

1. **Document-anchor adoption**
   - Source: `search.agentic_conversion`
   - Metric: count of events with `document_anchor_refs > 0`
2. **Manifest hit rate**
   - Source: `read_knowledge.agentic_v2`
   - Metric: avg `text_group_manifest_hit_rate` where `text_group_manifest_lookups > 0`
3. **Grouped read savings**
   - Source: `read_knowledge.agentic_v2`
   - Metrics: `text_group_window_reads`, `text_group_chunk_refs_covered`, `text_group_upload_refs_covered`
4. **Manifest miss watch**
   - Source: `read_knowledge.agentic_v2`
   - Metric: count/sum of `text_group_manifest_misses`

## Datadog log query templates

Use these as starting points (adjust service/env filters):

- Adoption:
  - `event:search.agentic_conversion @text_grouping_enabled:true @document_anchor_refs:[1 TO *]`
- Manifest lookups:
  - `event:read_knowledge.agentic_v2 @text_group_manifest_lookups:[1 TO *]`
- Misses:
  - `event:read_knowledge.agentic_v2 @text_group_manifest_misses:[1 TO *]`
- Covered ref savings:
  - `event:read_knowledge.agentic_v2 (@text_group_chunk_refs_covered:[1 TO *] OR @text_group_upload_refs_covered:[1 TO *])`

## Loki/Grafana filter templates

- `{app="pocketai-django"} |= "READ_KNOWLEDGE.AGENTIC_V2" |= "text_group_manifest_lookups="`
- `{app="pocketai-django"} |= "SEARCH.AGENTIC_CONVERSION" |= "document_anchor_refs="`

## Alert suggestions

- **High miss ratio**: `text_group_manifest_misses / max(text_group_manifest_lookups,1) > 0.20` for 15m.
- **No adoption regression**: `document_anchor_refs == 0` for 60m in production traffic windows.
- **Drop in savings**: sharp decline in `text_group_chunk_refs_covered` week-over-week.
