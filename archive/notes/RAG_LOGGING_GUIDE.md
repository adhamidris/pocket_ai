# RAG Snippet Content Logging - Implementation Summary

## Overview
Added full snippet content logging capability to analyze RAG search outputs in detail.

## Changes Made

### 1. Code Changes

#### `/apps/mcp/tools.py`
- Added `MCP_LOG_FULL_SNIPPET_CONTENT_DEFAULT = False` constant (line 77)
- Added `_mcp_log_full_snippet_content_enabled()` helper function (lines 88-89)
- Enhanced `_log_snippet_payloads()` function (lines 1657-1702) to include:
  - Full content from `payload.get("content")`
  - Summary from `payload.get("summary")`
  - Rows data from `payload.get("rows")` for table snippets
  - Character counts for each field

### 2. Settings Configuration

#### `/pocketai/settings.py`
- Added `MCP_LOG_FULL_SNIPPET_CONTENT` setting (line 157)
- Reads from environment variable `MCP_LOG_FULL_SNIPPET_CONTENT`
- Defaults to `false` for privacy/security

#### `/.env`
- Added `MCP_LOG_FULL_SNIPPET_CONTENT=true` (line 88)

## How It Works

When enabled, the logging system will now include in `rag.log`:

```python
{
  "label": "Purchasing Data – Purchasing Sales Data",
  "upload_id": "...",
  "chunk_id": "...",
  "read_state": "full",
  "read_required": true,
  "is_table_chunk": false,
  
  # NEW FIELDS (when MCP_LOG_FULL_SNIPPET_CONTENT=true):
  "full_content": "actual content sent to LLM...",
  "full_content_len": 4036,
  "summary": "summary text if available...",
  "summary_len": 200,
  "rows": [...],  # for table data
  "rows_count": 5
}
```

## Log Levels

You now have three logging flags:

| Flag | Purpose | Default |
|------|---------|---------|
| `MCP_LOG_PII` | Include PII in logs | `false` |
| `MCP_LOG_SNIPPET_PREVIEWS` | Include 200-char previews | `false` |
| `MCP_LOG_FULL_SNIPPET_CONTENT` | Include full content/rows/summary | `false` |

## Security Notes

⚠️ **Important**: When `MCP_LOG_FULL_SNIPPET_CONTENT=true`, full content including:
- Complete text snippets
- Table rows with all data
- Summaries

will be written to `var/logs/rag.log`. This may include:
- Customer data
- Product information  
- Sensitive business data

**Recommendation**: Only enable for development/debugging, not in production.

## Testing

To test the new logging:

1. Ensure server is running with the new settings
2. Make a query through the chat interface
3. Check `var/logs/rag.log` for the `search_knowledge.snippets` or `read_knowledge.performance` entries
4. Look for the new fields: `full_content`, `summary`, `rows`

## Example Log Entry

```log
2025-12-24 05:06:58,369 [INFO] apps.mcp.tools: mcp.trace [2025-12-24 05:06:58 EET] stage=search_knowledge.snippets business=... conversation=...
• intent=identifier query_len=18 snippet_count=1 snippets=[{
    'label': 'Purchasing Data – Purchasing Sales Data',
    'upload_id': '5fa11b18-86a8-4b42-97a8-453e76e91271', 
    'chunk_id': '33c28bf4-215c-41f3-8df4-a9adc2b8b706',
    'read_state': 'full',
    'read_required': True,
    'is_table_chunk': False,
    'full_content': 'Product Code,Product Name,Category,Price\n20666,Aspirin 100mg,Pharmaceuticals,5.99\n...',
    'full_content_len': 4036,
    'rows': [{...}],
    'rows_count': 148
}]
```

## Rollback

To disable full content logging:

```bash
# In .env
MCP_LOG_FULL_SNIPPET_CONTENT=false
```

Then restart the server.

## Next Steps

1. Test with a real query
2. Analyze the logged snippets
3. Identify any issues with search results
4. Adjust RAG parameters if needed (weights, thresholds, etc.)
