**TL;DR**

- Define repository interfaces for the 4-step web registration wizard: create/read/update of registration sessions, user, business, agent(+traits), and knowledge items. [Assumption]
- Specify tenant-scoped query patterns: all reads/writes accept `business_id` once established in Step 2; pre-tenant ops use `registration_id` + `user_id`. [Confirmed multi-tenancy intent; scoping details Assumption]
- Provide filters, pagination, sorting, eager-loading to avoid N+1 (e.g., Agent with Traits; Knowledge item with typed child). [Assumption]
- Recommend supporting indexes aligned to query access paths (sessions, agents, knowledge, memberships). [Assumption]
- Define error conditions and repository-level constraints (timeouts, default limits, idempotent upserts) to ensure consistency. [Assumption]

**Frontend Evidence Used**

- Web Register page `src/pages/Register.tsx` with step keys `'form' | 'business' | 'agent' | 'uploads'` and fields: `firstName`, `email`, `password`, `confirmPassword`; business: `businessName`, `industry`, `specifyIndustry`, `lineOfBusiness[]`, `lineOfBusinessCustom[]`, `country`, `website`; agent: `agentName`, `agentTitle`, `agentTone`, `agentTraits[]`, `agentEscalation`; uploads: `uploadsVision[]`, `uploadsMission[]`, `uploadsCatalog[]`, `uploadsFaqs[]`, `uploadsKb[]`, `uploadsSops[]`, `uploadsTc[]` plus corresponding `...Url` single inputs. [Confirmed]
- Models phase doc `phases/registeration-web/models-analysis.md` — entities and enums: `users`, `registration_sessions`, `businesses`, `business_niches`, `user_business_memberships`, `agents`, `agent_traits`, `knowledge_items`, `knowledge_item_{files,urls,texts}`; wizard steps enum (`business_profile`, `agent_setup`, `knowledge_uploads`, `completed`). [Confirmed]
- UI agent options lists (labels) differ from model enums for tone/traits (e.g., UI tone includes 'Concise', models list does not). Mapping needed. [Assumption]
- Registration session continuity via `registration_id` and TTL (7 days confirmed in models doc Q&A). [Confirmed]

**Phase Requirements (Repository)**

- Scope
  - Provide DB access layer contracts only (no business rules or HTTP). [Confirmed]
  - All repository methods that act on tenant data MUST take `business_id: UUID`. For pre-tenant steps, use `registration_id` and `user_id` scoping. [Assumption]
  - Return plain domain DTOs hydrated from SQL rows (Pydantic/ORM models belong to other layers). [Assumption]

- Cross-entity patterns
  - Pagination: offset-limit with `limit` default 20, max 100; `offset` default 0. [Assumption]
  - Sorting: allow `sort_by` with `created_at` (default desc) and entity-specific fields (see below). [Assumption]
  - Eager-loading: dedicated getters to hydrate children (agent→traits; knowledge→typed child). [Assumption]
  - Concurrency: use optimistic check on `updated_at` or integer `version` where available; expose `expected_updated_at` in update methods. [Assumption]
  - Idempotency: support dedupe on natural keys in registration scope (e.g., knowledge URL per `business_id`). [Assumption]
  - Query timeouts: repository enforces statement timeout 2s per call (configurable). [Assumption]

- Repositories and method contracts

  1) RegistrationSessionsRepository
  - create_session(user_id: UUID, initial_step: registration_step='business_profile', ttl_days: int=7) -> RegistrationSession [Assumption]
  - get_session(registration_id: UUID, for_user_id: UUID) -> RegistrationSession | NotFound | Expired [Assumption]
  - attach_business(registration_id: UUID, business_id: UUID) -> RegistrationSession (idempotent; no-op if already set) [Assumption]
  - update_progress(registration_id: UUID, current_step: registration_step, state_patch: JSON|null, expected_updated_at?: ISO8601) -> RegistrationSession | Conflict [Assumption]
  - mark_completed(registration_id: UUID) -> RegistrationSession [Assumption]
  - list_sessions(for_user_id: UUID, status: 'active'|'expired'|'all'='active', limit?: int, offset?: int, sort_by?: 'updated_at'|'created_at', order?: 'asc'|'desc'='desc') -> RegistrationSession[] [Assumption]
  - expire_sessions(before: ISO8601) -> int affected (utility; typically job-owned) [Assumption]
  - compute_step_completion(registration_id: UUID) -> { business_created: bool, agent_configured: bool, knowledge_count: int } [Assumption]

  Access patterns & Indexes
  - Reads by `id` with not-expired filter; listing by `user_id` ordered by `updated_at desc`.
  - Indexes: `(id)`, `(user_id, updated_at desc)`, partial index on `expires_at > now()` for active lookups. [Assumption]

  2) UsersRepository (pre-tenant)
  - find_by_email(email_lower: text) -> User|null [Assumption]
  - create_user(first_name: text, email: text, password_hash: text|null, auth_provider: 'password'|'google', email_verified: bool=false) -> User [Assumption]
  - mark_email_verified(user_id: UUID) -> void [Assumption]
  - Note: email normalization to lowercase handled before repository. [Assumption]

  3) BusinessesRepository
  - create_business(name: text, industry_code: text, created_by_user_id: UUID) -> Business [Assumption]
  - get_business(business_id: UUID) -> Business | NotFound [Assumption]
  - list_by_creator(user_id: UUID, limit?: int, offset?: int) -> Business[] [Assumption]
  - Access patterns & Indexes: index `(industry_code)`, `(created_by_user_id, created_at desc)`. [Assumption]

  4) BusinessNichesRepository
  - replace_niches(business_id: UUID, niche_codes: text[]) -> void (delete missing, insert new) [Assumption]
  - list_niches(business_id: UUID) -> { niche_code: text }[] [Assumption]
  - Index/PK: `(business_id, niche_code)` PK; lookups by `business_id`. [Assumption]

  5) MembershipsRepository
  - add_owner(user_id: UUID, business_id: UUID) -> void (idempotent on PK (user_id,business_id)) [Assumption]
  - get_roles(business_id: UUID, user_id: UUID) -> { role: 'owner'|'admin'|'agent' }[] [Assumption]
  - list_members(business_id: UUID, role?: 'owner'|'admin'|'agent', limit?: int, offset?: int) -> { user_id: UUID, role: ... }[] [Assumption]
  - Indexes: `(business_id, role)`, PK `(user_id, business_id)`. [Assumption]

  6) AgentsRepository
  - create_agent(business_id: UUID, name: text, role: agent_role, tone: agent_tone, escalation_rule: escalation_rule) -> Agent [Assumption]
  - get_agent(business_id: UUID, agent_id: UUID) -> Agent | NotFound [Assumption]
  - get_agent_with_traits(business_id: UUID, agent_id: UUID) -> AgentWithTraits [Assumption]
  - list_agents(business_id: UUID, q_name?: text, role?: agent_role, limit?: int, offset?: int, sort_by?: 'created_at'|'name', order?: 'asc'|'desc'='desc') -> Agent[] [Assumption]
  - update_agent(business_id: UUID, agent_id: UUID, patch: { name?: text, role?: agent_role, tone?: agent_tone, escalation_rule?: escalation_rule }, expected_updated_at?: ISO8601) -> Agent | Conflict [Assumption]
  - replace_traits(business_id: UUID, agent_id: UUID, trait_codes: agent_trait[]) -> void [Assumption]
  - Indexes: `(business_id)`, `(business_id, name)` for search; note: models Q&A says agent names can duplicate per business, so no unique index. [Confirmed]

  7) KnowledgeItemsRepository
  - create_url(business_id: UUID, display_name: text|null, url: https_url, language: bcp47|null, created_by_user_id: UUID) -> KnowledgeItemUrl [Assumption]
  - create_file(business_id: UUID, display_name: text|null, storage_path: text, filename: text, content_type: text|null, size_bytes: bigint, checksum_sha256?: text|null, language: bcp47|null, created_by_user_id: UUID) -> KnowledgeItemFile [Assumption]
  - create_text(business_id: UUID, display_name: text|null, text_content: text, language: bcp47|null, created_by_user_id: UUID) -> KnowledgeItemText [Assumption]
  - list_items(business_id: UUID, status?: knowledge_status[], source_type?: knowledge_source[], q_name?: text, limit?: int, offset?: int, sort_by?: 'created_at'|'status', order?: 'asc'|'desc'='desc') -> KnowledgeItem[] [Assumption]
  - get_item_with_detail(business_id: UUID, knowledge_item_id: UUID) -> KnowledgeItemWithDetail | NotFound [Assumption]
  - update_status(business_id: UUID, knowledge_item_id: UUID, status: knowledge_status) -> void [Assumption]
  - Indexes: `(business_id, status, created_at desc)`, `(business_id, source_type, created_at desc)`; recommend unique dedupe on `(business_id, source_type, url)` at `knowledge_item_urls` for idempotent URL attachments. [Assumption]

- Step key mapping (UI ↔ Model)
  - UI: `'form' | 'business' | 'agent' | 'uploads'`. [Confirmed]
  - Model: `'business_profile' | 'agent_setup' | 'knowledge_uploads' | 'completed'`. [Confirmed]
  - Repository will persist model step values; service/router map UI keys → model enum. [Assumption]

**Data Contracts (JSON examples)**

- RegistrationSession (repository return)
  {
    "id": "b6b7c2ce-2a04-4d24-9c24-2a0830b0c2a1",            // [Assumption]
    "user_id": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21",       // [Assumption]
    "business_id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",   // [Assumption]
    "current_step": "agent_setup",                            // [Assumption]
    "expires_at": "2025-01-19T09:10:11Z",                    // [Confirmed TTL concept; value Assumption]
    "updated_at": "2025-01-12T10:00:00Z"                      // [Assumption]
  }

- AgentWithTraits (eager-loaded)
  {
    "agent": {
      "id": "3f981f0f-2f63-4ea4-9c7c-bb0d9d2e6c9a",          // [Assumption]
      "business_id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7", // [Confirmed]
      "name": "Pocket Assistant",                              // [Confirmed]
      "role": "support",                                       // [Assumption]
      "tone": "friendly",                                      // [Assumption]
      "escalation_rule": "on_fallback",                        // [Confirmed]
      "created_at": "2025-01-12T09:10:11Z",                    // [Assumption]
      "updated_at": "2025-01-12T09:12:00Z"                     // [Assumption]
    },
    "traits": [
      { "trait_code": "empathetic" },                           // [Confirmed trait concept; value Assumption]
      { "trait_code": "proactive" }                             // [Assumption]
    ]
  }

- KnowledgeItemWithDetail (URL example)
  {
    "item": {
      "id": "f5b1d6ad-7ef8-4198-8ea8-9cb7a7f52d9e",           // [Assumption]
      "business_id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",  // [Confirmed]
      "source_type": "url",                                     // [Assumption]
      "status": "pending",                                      // [Assumption]
      "display_name": "Help Center",                            // [Assumption]
      "language": "en",                                         // [Assumption]
      "created_by_user_id": "2c9f7f...",                        // [Assumption]
      "created_at": "2025-01-12T09:10:11Z"                      // [Assumption]
    },
    "url": { "url": "https://example.com/docs/getting-started" } // [Confirmed URL idea; exact value Assumption]
  }

- Business with niches
  {
    "business": {
      "id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",           // [Assumption]
      "name": "Pocket AI Studio",                               // [Confirmed]
      "industry_code": "industry:smb-software",                 // [Confirmed concept; value Assumption]
      "created_by_user_id": "2c9f7f4e-...",                     // [Assumption]
      "created_at": "2025-01-12T09:10:11Z"                      // [Assumption]
    },
    "niches": [ { "niche_code": "niche:customer-support" } ]    // [Confirmed concept; value Assumption]
  }

Notes
- Field constraints (regex/length) follow models analysis: `industry_code ~ ^industry:[a-z0-9-]{2,50}$`, `niche_code ~ ^niche:[a-z0-9-]{2,50}$`, `display_name ≤ 120`, `filename ≤ 255`, `https://` URL, file `size_bytes ≤ 20MB`. [Confirmed concept; specific bounds Assumption]

**Tenancy & Permissions**

- Resolution of `business_id`
  - Step 1 (account creation): pre-tenant; repository uses `users` and `registration_sessions` scoped by `user_id`. [Confirmed concept]
  - Step 2+: a `business_id` is created and attached to the session; all subsequent repository calls require `business_id`. [Assumption]
- Roles
  - `user_business_memberships` grants roles: OWNER/ADMIN/AGENT; registration inserts OWNER for the creator (idempotent). [Assumption]
- Cross-tenant protections
  - Every repository method for tenant data requires and filters by `business_id`; `user_id` is not sufficient by itself for tenant reads/writes. [Assumption]
  - Agents/Knowledge queries always include `WHERE business_id = $1`. [Confirmed requirement intent]

**Constraints & Quality Gates**

- Pagination: default `limit=20`, max `limit=100`; enforce `offset ≤ 10,000` to prevent deep scans. [Assumption]
- Query timeouts: 2s per call; timeouts surface as `code: 'db_timeout'`. [Assumption]
- N+1: use `get_agent_with_traits` and `get_item_with_detail` eager-loaders; avoid per-row child fetch loops. [Assumption]
- Idempotency:
  - `add_owner` no-ops on existing PK.
  - `create_url` dedupes by `(business_id,url)` when `source_type='url'` (return existing). [Assumption]
- Error shape propagated upward: `{ code: string, message: string, details?: object }` with common codes: `not_found`, `conflict`, `expired`, `db_timeout`, `validation`. [Assumption]

**Dependencies & Stubs**

- Transaction/Unit-of-Work interface to group multi-entity writes per step (service-owned). [Assumption]
- Clock provider (UTC now) to compute/compare `expires_at`. [Assumption]
- UUID generator for ids (DB/defaults). [Assumption]
- Normalization utilities in service layer to map UI labels → `industry_code`, `niche_code`, and enum values for `agent.role`, `agent.tone`, `agent_traits`. [Assumption]
- Storage adapter for file uploads (for `knowledge_item_files`) — repository only persists metadata. [Assumption]
- OAuth/OpenID adapter for Google signup — repository only handles `users` persistence. [Assumption]

**Risks & Tradeoffs**

- UI labels vs model enums mismatch (tone/traits); requires deterministic mapping to avoid invalid enum insertions. [Assumption]
- Registration pre-tenant scoping: until `business_id` exists, session operations cannot be tenant-scoped; mitigated by strict `user_id + registration_id` checks and TTL. [Assumption]
- Knowledge URL dedupe: without a DB unique index, duplicates may slip under concurrency; recommending unique constraint on `(business_id,url)` in `knowledge_item_urls`. [Assumption]
- Agent name duplicates allowed per Q&A; search UX may surface multiple; repository must not rely on name uniqueness. [Confirmed]
- Large `state` JSON in sessions can bloat row; repository should keep state minimal and push heavy derived data to tenant tables. [Assumption]

**Acceptance Criteria**

- Repository interfaces defined for all registration steps covering create/read/update and list operations with tenant scoping (`business_id`). [Confirmed]
- Filters, pagination, and sorting specified for sessions, agents, knowledge items, memberships. [Assumption]
- Eager-loading functions specified to avoid N+1 for agent traits and knowledge typed details. [Assumption]
- Recommended indexes listed that support the declared query patterns. [Assumption]
- Error codes and timeouts defined at repository boundary; idempotency behaviors documented. [Assumption]

**Open Questions**

- Exact enum values to persist for `agent.role`, `agent.tone`, and `agent_traits`: confirm final set and mapping from UI labels (e.g., UI tone includes 'Concise', models list does not). Can we provide a mapping table? [Assumption] -- Answer: follow UI inspirations.
- Confirm `registration_sessions` columns: presence of `updated_at`/`version`, `state` JSON usage, and final `current_step` enum values. [Assumption] - do the needed
- Knowledge URL dedupe: should we enforce a DB unique index `(business_id,url)` for `knowledge_item_urls`? [Assumption] - do the right thing.
- For uploads step, current web UI only attaches URLs. Should repository support file/text types in this phase, or defer until the UI exposes them? [Assumption] - have the backend ready to accept uploads.. updating the frontend would be simple later.
- Steps completion counter: is the source of truth `registration_sessions.state` vs. derived from tenant tables (business present, agent exists, knowledge count)? What is the weighting to compute “% complete”? [Assumption] - all input data completes the registeration process, we should have this is as the base for counting.
- TTL: models Q&A confirms 7 days — should repository enforce a grace period or hard-expire (reads return `expired`)? [Confirmed TTL concept; enforcement Assumption]  -- hard expire.
- Additional audit fields: models Q&A mentioned storing `created_by_user_name`. Should repository expose reads that include both id and display name for created_by? [Assumption] - yes it should.

