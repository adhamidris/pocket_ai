# PocketAI SaaS — Technical Overview

This document explains how the PocketAI platform works from a technical perspective. It is intended for engineers and repo contributors who need a reliable mental model of the system, the major components, and the core invariants (tenant isolation, privacy, auditability, and predictable retrieval).

If you want a business-only description (for sales, onboarding, or investors), use `docs/product/saas_brief.md`.

**Status note:** Platform is in **beta**. MCP is the only active orchestrator path. Agentic read v2 is enabled (`MCP_AGENTIC_READ_V2_ENABLED=true`), and the LLM tool surface is intentionally limited in agentic mode.

---

## Goals
- Multi-tenant: every tenant (business) can create multiple AI agents and publish them to end users.
- Knowledge-grounded answers: agents answer based on tenant-provided documents (RAG).
- Safety by default: protect sensitive data even under tenant misconfiguration; support verified lookup when enabled.
- Scalable portal traffic: support high fanout (1000+ concurrent visitors) with bounded latency and predictable retrieval.
- Auditability: keep an access trail for compliance/disputes without retaining deleted document content in logs.

## Non-goals (explicit)
- The public portal agent should not run advanced spreadsheet-style computation that could leak private rows. “True querying” is an internal/admin capability, not for anonymous portal visitors.

---

## Glossary (platform terms)
- **Tenant / Business**: a single customer of PocketAI. Implemented as a `BusinessProfile` and used as the primary isolation boundary.
- **Agent**: an AI persona/configuration owned by a tenant (tone, role, instructions). End users talk to an agent.
- **Collection**: a logical grouping of knowledge uploads. Agents can be assigned multiple collections to scope retrieval.
- **Knowledge Upload**: a tenant-provided source (PDF/DOCX/TXT/CSV/XLSX/JSON/image/scanned PDF).
- **Chunk**: a retrievable unit produced during ingestion (text, table parent/child, page blocks).
- **Snippet**: a serialized “evidence packet” returned to the LLM from tools (summary/preview/full + provenance + read hints).
- **Orchestrator**: the component that runs a “turn” (one user message) and manages tool calls + final answer.
- **Tools**: backend capabilities the LLM can call (search knowledge, read evidence, CRM operations, etc.).
- **Verified lookup**: a privacy gate requiring identifiers/OTP before exposing sensitive fields.

---

## High-level architecture

### Components
- **Frontend chat portal**: the end-user UI (and optionally embeddable widget) that streams responses.
- **Django API/backend**: multi-tenant API, orchestration, tools, CRM objects, and ingestion coordination.
- **LLM provider**: model completion + tool calling (current dev uses DeepSeek; production can support multiple providers).
- **Knowledge/RAG subsystem**:
  - Ingestion pipeline (extract → normalize → chunk → embed/index → table/dataset artifacts)
  - Retrieval pipeline (hybrid search + table-aware retrieval + reranking + provenance)
- **Storage**:
  - Postgres: primary system of record (tenancy, conversations, uploads metadata, tables, chunks)
  - Cache (dev: LocMem; production: Redis)
  - Object storage (production: Azure Blob): raw uploads + extracted artifacts
- **Observability**: structured logs + tracing spans + metrics (p95 latency and tool performance).

### Request flow (one chat turn)
1. Visitor sends a message to the chat portal.
2. Backend records the customer message to the conversation timeline (tenant-scoped).
3. Orchestrator builds a prompt window (system + recent history) and calls the LLM provider with tools enabled.
4. LLM may call tools (e.g., `search_knowledge`, `read_knowledge`) to retrieve evidence.
   - In agentic mode (default), table/dataset tools are not exposed to the LLM.
5. Orchestrator executes tools server-side and returns tool outputs to the LLM.
6. LLM streams the final answer to the visitor.
7. A planner step may run after the answer to propose internal actions (cases/leads/appointments) and extractions.

---

## Tenancy and isolation
Tenant isolation is not a feature; it is a system invariant.

### Isolation boundaries
- **Database queries**: every query must be scoped by tenant (`business_profile_id`) or by objects already tenant-scoped.
- **Tool execution**: tools must never read across tenants; tool handlers execute inside a tenant context.
- **Caches**: cache keys must include tenant identity (and collection scope when relevant).
- **Object storage**: per-tenant prefixing and access control; delete/purge must remove per-tenant artifacts.

### Collections and retrieval scope
Collections provide a first-class scoping mechanism:
- Uploads can belong to one or multiple collections.
- Agents can be assigned multiple collections.
- Retrieval should filter to the agent’s active collections by default to improve precision and reduce cross-topic bleed.

---

## Orchestration model (MCP tool loop)
The orchestration design aims to be:
- **Tool-efficient**: prefer one well-formed search over many retries.
- **Deterministic**: bounded tool budget per visitor message.
- **Evidence-first**: respond as soon as evidence is sufficient; don’t narrate internal steps.

### Core tool pattern
Typical “knowledge question”:
1. `search_knowledge` (batched `queries[]` variants)
2. Targeted read depending on source type:
   - `read_knowledge` for document/chunk reads (agentic v2 refs)
   - Dataset/table operations are handled server‑side; the LLM only uses `search_knowledge` + `read_knowledge` in agentic mode.
3. Final answer (tools disabled in the final pass when possible)

### Tool budgets and policies
The platform enforces constraints to avoid runaway turns:
- “One search per visitor message” policy (prevents infinite search loops).
- Prompt/context governor (prevents context overflow).
- Chunk/page read budgets (prevents excessive document reads).
- Latency/SLO warnings (logs identify slow phases and hot paths).

Important: tool budgets must match prompt instructions. If the prompt encourages retries but policy blocks them, the model will thrash and answer quality degrades.

---

## Knowledge system (RAG)

### Ingestion outputs
The ingestion pipeline produces a normalized, searchable representation:
- **Text chunks**: paragraph/section blocks with page provenance.
- **Table artifacts**:
  - Tables (`KnowledgeUploadTable`)
  - Rows (`KnowledgeUploadTableRow`)
  - Cells (`KnowledgeUploadTableCell`)
  - Parent/child table chunks (schema-aware chunking)
- **Embeddings**: vector representations per chunk (for semantic search).
- **Lexical index**: full-text search (FTS) over chunk text for fast keyword lookup.
- **Metadata**: ingestion stats, extraction confidence, format signals, and privacy hints.

### Retrieval modes (conceptual)
Retrieval is hybrid and table-aware:
- **Alias / exact match**: fast path for direct references and known labels.
- **Lexical (FTS)**: keyword search for policy/fees/terms queries.
- **Vector (semantic)**: similarity search for paraphrases and long-form questions.
- **Table-aware routing**: when a query looks like “fees/rates/pricing/compare”, table chunks are prioritized and can trigger targeted row expansion.

### Retrieval invariants (current)
- Rank by relevance only (hybrid + rerank + exact-match boosts), then dedupe/group evidence.
- No representation quotas (no forced text/table slot balancing after ranking).
- No forced full-table/document reads injected from intent heuristics.
- `search_knowledge.limit` is honored unless prompt/token budgets require compaction.

### Snippet contract (what the LLM receives)
Tool outputs are intentionally constrained:
- Provide small, provenance-rich snippets (summary/preview/full).
- Attach `read_hint` so the model (or the backend) can request deeper reads deterministically.
- Avoid exposing raw PII fields unless verified lookup conditions are satisfied.

---

## Tabular querying (internal capability)
The platform can support dataset-style operations (filters/sorts/top‑k/sum/avg/date ranges) for internal tenant workflows.

Constraints:
- **Not LLM‑facing in agentic mode** (tool surface is restricted).
- Available only in non‑agentic/legacy/internal flows when enabled.
- Must be gated by role (tenant admin) and/or verification when it touches sensitive datasets.
- Must produce explainable outputs: what was filtered, how it was computed, and which dataset/columns were used.

---

## Safety and privacy model

### Default-safe behavior
The platform should protect users even if the tenant forgets to configure privacy:
- Detect sensitive columns/fields during ingestion (PII heuristics + column schema analysis).
- Apply conservative defaults (mask/deny) until verified lookup is enabled for that knowledge source.
- Enforce a platform override to refuse disclosure when content appears sensitive.

### Verified lookup (OTP + identifiers)
Verified lookup is a capability that tenants can enable:
- Triggered only when the conversation requires customer-specific data.
- Supports multi-channel OTP (email/SMS/WhatsApp) via external providers.
- Once verified, retrieval may be scoped to matching records/uploads and may expose allowed fields.

Current repo behavior:
- This deployment is knowledge-RAG (bank fees/policies), not customer-service identity lookups.
- The legacy “verified lookup / OTP / PII masking” stack was removed to avoid masking legitimate bank contact details (emails/phones) in answers/evidence.

---

## Audit logging
Audit logs are retained for accountability and dispute response.

Key principle: **audit logs should not store raw document content or raw PII**, otherwise “deletion/purge” becomes impossible.

Recommended audit event payload:
- tenant id, agent id, conversation id, timestamp
- tool name + parameters (redacted)
- upload/chunk/table identifiers accessed
- result counts + hashes of evidence packets (for proof without retaining content)

---

## Document lifecycle (overwrite + delete)

### Overwrite in place
When a tenant overwrites an upload:
- Create a new ingestion run for the updated source.
- Replace/refresh derived artifacts (chunks, embeddings, table rows/cells, indexes) so retrieval reflects the latest version.
- Keep the upload identity stable for the dashboard and collections (implementation can version internally, but the product behaves as “updated doc”).

### Deletion and purge
When a tenant deletes an upload:
- Remove the upload record (or mark deleted) and all derived retrieval artifacts from active indexes/caches.
- Ensure it no longer influences answers.
- Preserve audit logs (metadata-only), not the deleted content.

---

## Production reference architecture (Azure, active-passive)
This section describes the recommended production shape. Not all items are implemented in the repo yet; treat it as the target operating model.

### Regions
- **Primary**: UAE North
- **DR**: Saudi Central
- Tenant data is region-pinned; DR is for failover (active-passive), not active-active traffic.

### Core managed services
- **Azure Front Door (or Cloudflare)**: global edge + WAF + tenant routing.
- **Container hosting**: Docker-based deploy (e.g., Azure Container Apps; AKS if/when you need full Kubernetes).
- **Azure Database for PostgreSQL (Flexible Server)**: primary relational store.
- **Azure Cache for Redis**: shared cache and rate limiting (replace LocMemCache).
- **Azure Blob Storage**: raw uploads and derived artifacts.
- **Azure Document Intelligence**: ingestion-time document layout + OCR + table extraction (especially for scanned PDFs).
- **OTP providers**:
  - Email/SMS: Azure Communication Services
  - WhatsApp: Twilio or Meta Cloud API

### Retrieval backend options
- **Current (repo)**: Postgres-backed hybrid retrieval (FTS + vector + table artifacts).
- **Optional (repo, production-friendly)**: Azure AI Search for document retrieval (hybrid text+vector), while keeping Postgres as the source of truth (uploads/chunks/tables) and using Azure only for candidate retrieval.
  - Enable with `RAG_SEARCH_BACKEND=azure` and Azure search env vars (`AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_ADMIN_KEY`, `AZURE_SEARCH_INDEX_NAME`).
  - Ops commands: `python manage.py azure_search_ensure_index` and `python manage.py azure_search_backfill --business-id <uuid>`.

---

## Performance targets and operating principles
- Retrieval must be bounded and observable. Every “slow” request must have a clear, attributable component cost (FTS, vector, rerank, table, etc.).
- Avoid thundering-herd patterns:
  - Don’t run unbounded query fanout in parallel.
  - Precompute heavy table context during ingestion, not per-query.
  - Cache per-tenant search artifacts in Redis with safe invalidation on overwrite/delete.

### Troubleshooting flow (ingestion/retrieval)
1. Validate ingestion coverage first (pages/tables/chunks/issues in Knowledge Visualizer).
2. If coverage is wrong, rebuild canonical artifacts before tuning retrieval:
   - `python manage.py rebuild_canonical_knowledge_chunks --upload-id <uuid>`
3. If coverage is correct but answers are wrong, run evaluation harness:
   - `python manage.py run_rag_eval --baseline --output <path>`
4. Compare quality + latency deltas before changing ranking:
   - Recall/source accuracy regressions block release.
   - Payload reductions are only accepted when recall is preserved.

Suggested SLOs (product-level):
- p95 retrieval: 5–8s for simple queries; 15–25s for complex multi-part queries.
- Hard upper bounds: enforce timeouts and return partial-but-honest results rather than hanging.

---

## Where to look in the repo
- Orchestration + tool loop: `apps/mcp/`
- Tool handlers and logging: `apps/mcp/tools.py`
- Knowledge search service: `apps/rag/ai_orchestrator.py`
- Knowledge ingestion: `apps/knowledge/knowledge_ingestion.py`
- Knowledge models (uploads/tables/collections): `apps/accounts/models.py`
- Conversations + message persistence: `apps/conversations/`
- LLM provider integration: `apps/llm/`
- Operational logs (dev): `var/logs/`
