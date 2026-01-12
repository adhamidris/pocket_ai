# Agentic RAG Retrieval Fix Plan

## Plan
1. Align agentic tool contract: update prompt + tool schema so `search_knowledge` returns readable IDs and `read_document` accepts `ids` with `max_chars`.
2. Map table-direct results to row chunk IDs and include page hints for stable `search -> read` chaining.
3. Prevent result collapse by deduping on stable identity (chunk ID or snippet ID) instead of `(chunk_id, upload_id)` only.
4. Disable deprecated auto-structure behavior in agentic mode and keep agentic search results parseable for table discovery.
5. Add regression coverage for table-direct retrieval to ensure table rows can be read deterministically.

## Business POV
- Scenario: A bank customer asks for all credit card issuance fees. Expected UX: agent returns a complete list across all tables; no missing cards. Success: 100% of listed cards have fees; no "unknown" for known items.
- Scenario: A customer asks about a specific card (e.g., Heya). Expected UX: agent finds the exact row quickly, reads the table, and answers confidently. Success: correct fee with no extra follow-up.
- Scenario: A tenant uploads a new pricing PDF with multiple tables. Expected UX: agent discovers all tables via search and can read the right sections without manual retries. Success: search->read tool usage is consistent; support tickets about missing rows drop.
- Scenario: A large PDF with many tables. Expected UX: agent reads only what it needs (targeted IDs) and avoids timeouts. Success: p95 latency stays within retrieval SLO; no tool budget errors.
