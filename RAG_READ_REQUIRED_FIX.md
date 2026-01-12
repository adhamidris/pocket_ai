# RAG Read Required Normalization

## Goal
Normalize `read_required` so it means **"the snippet itself shows signs of incomplete context"** (e.g., truncation), while keeping the final decision fully LLM-driven. This removes backend forcing and avoids keyword-based gating.

## What Changed

### 1) Backend now computes `read_required` from snippet-only sufficiency signals
`read_required` is set per snippet when the snippet is **summary/preview** and shows evidence that the content itself is incomplete, for example:
- truncation or partial indexing flags
- table truncation / partial table diagnostics

Each snippet also carries `read_required_reasons` for debugging.

### 2) Orchestrator no longer forces reads
The backend no longer blocks a final answer if a read was not performed. This keeps the experience LLM-driven instead of a deterministic chatbot gate. We now log cases where `read_required=true` but no read occurred for observability.

### 3) Prompts align with the new contract
`read_required=true` is **advisory** (a warning that the snippet may be incomplete), so the assistant should decide whether more reading is needed based on the visitor’s question. `read_required=false` does not block reading if the visitor explicitly asks for missing detail.

## Why This Is Better
- **Faster**: fewer unnecessary reads for simple lookups.
- **Consistent**: no brittle keyword gates.
- **LLM-driven**: the model decides if additional evidence is required.

## Manual Verification
Suggested quick checks:
1. Identifier lookup with complete snippet → no read.
2. Table preview with truncation note → read before answer.
3. “List all” request → uses structure/table tools, not partial snippets.

## Rollout Notes
If needed, the new `read_required` heuristic can be feature-flagged. Forced-read gating is removed unconditionally.
