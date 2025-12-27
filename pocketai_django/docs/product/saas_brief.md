Context for this codebase (please read before changing anything)

This repo is a multi-tenant B2B SaaS platform where each business (tenant) gets its own AI agent for support/sales. Tenants upload their knowledge (docs, PDFs, CSVs, etc.), and end users talk to the agent through a chat portal or embedded widget. The backend is Django + PostgreSQL.

The AI agent works with RAG + tools:

It searches tenant-specific knowledge and reads snippets.

It can call tools to manage CRM-style objects: customers, cases, leads, appointments, etc.

Then it returns a final answer to the end user (usually via streaming).

When you change or add code, please:

Always keep tenant isolation (no cross-tenant data leakage).

Use the existing RAG + tool patterns instead of hard-coding special logic.

Keep flows simple and product-y: clear UX for non-technical business owners, fast and safe behavior for end users.

Document understanding principles (RAG):

Preserve structure before chunking: tables must be extracted as rows/columns with headers, not flattened text.

Chunk schema-aware: emit header+row child chunks and keep full-table parents for context.

Use confidence gating for tough layouts: route low-confidence tables to higher-fidelity extraction.

Keep provenance: every answer should trace back to a page anchor and table cell coordinates.
