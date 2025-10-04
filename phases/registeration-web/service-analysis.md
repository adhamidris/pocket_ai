**TL;DR**

- Define registration service orchestration for 4-step web wizard: account/start session, business profile, agent config, and knowledge link attachments. [Assumption]
- Enforce multi-tenant scoping: all post–Step 2 operations require and filter by `business_id` (UUID). [Confirmed intent]
- Guarantee idempotency and atomicity: per-step transactions; dedupe on URL attachments and session→business link. [Assumption]
- Maintain progress invariants: compute and persist `steps_completed/total_steps` and `current_step` transitions without regression. [Assumption]
- Normalize and map UI labels to internal enums/codes via catalog mappers (industry, role, tone, traits, escalation). [Assumption]

**Frontend Evidence Used**

- Register web page `src/pages/Register.tsx` with steps `'form' | 'business' | 'agent' | 'uploads'` and fields: [Confirmed]
  - Step 1 (form): `firstName`, `email`, `password`, `confirmPassword`; “Continue with Google”. [Confirmed]
  - Step 2 (business): `businessName`, `industry`, `specifyIndustry`, `lineOfBusiness[]`, `lineOfBusinessCustom[]`, `country`, `website`. [Confirmed]
  - Step 3 (agent): `agentName`, `agentTitle`, `agentTone`, `agentTraits[]`, `agentEscalation`. [Confirmed]
  - Step 4 (uploads): `uploadsVision[]`, `uploadsMission[]`, `uploadsCatalog[]`, `uploadsFaqs[]`, `uploadsKb[]`, `uploadsSops[]`, `uploadsTc[]` with respective `...Url` inputs for adding links. [Confirmed]
- Models baseline: `phases/registeration-web/models-analysis.md` entities/enums and `registration_sessions` lifecycle; `businesses`, `agents(+traits)`, `knowledge_items(+urls/files/texts)`. [Confirmed]
- Repository contracts: `phases/registeration-web/repository-analysis.md` (sessions, users, business/niches, memberships, agents, knowledge items; pagination/defaults; error shape). [Confirmed]

**Phase Requirements (Service)**

- Responsibilities
  - Orchestrate multi-entity operations per wizard step using repository APIs inside transactions. [Assumption]
  - Enforce business rules, invariants, idempotency, and step transitions (`current_step`, `steps_completed`, `total_steps`). [Assumption]
  - Normalize/map UI inputs to model enums/codes (industry_code, niche_code, agent role/tone/traits, escalation_rule). [Assumption]
  - Surface repository errors as service errors with `{ code, message, details? }` unchanged. [Confirmed error shape]

- Service Operations
  1) start_registration (Step 1)
  - Input: `{ firstName, email, password? | provider: 'google', registrationHints? }`. [Assumption]
  - Flow: normalize email→lower; if password path: hash handled by upstream auth module [Dependency]; ensure `confirmPassword` already validated by UI; check existing user; create user if needed; create `registration_session` with TTL=7d; return `registration_id`, user summary, `nextStep: 'business'`. [Assumption]
  - Idempotency: calling again with same email returns existing user and creates a new session only if last session expired; otherwise returns active session. [Assumption]

  2) upsert_business_profile (Step 2)
  - Input: `{ registrationId, userId, businessName, industry, specifyIndustry, lineOfBusiness[], lineOfBusinessCustom[], country?, website? }`. [Confirmed fields; mapping Assumption]
  - Flow (single transaction): resolve and map `industry`→`industry_code` and `lineOfBusiness[]`→`niche_code[]`; create `business`; insert OWNER membership; attach session to `business_id`; replace niches to match desired set; update session `current_step='agent_setup'`, bump `steps_completed` to ≥2. [Assumption]
  - Idempotency: if session already linked to a business, return that business (conflict if different); `replace_niches` ensures eventual consistency. [Confirmed repo behavior]

  3) configure_agent (Step 3)
  - Input: `{ businessId, userId, agentName?, agentTitle?, agentTone?, agentTraits[], agentEscalation? }`. [Confirmed fields]
  - Flow (single transaction): map `agentTitle`→`AgentRole`, `agentTone` label→enum, `agentTraits[]` labels→enums; create agent when at least one of name/role/tone provided; replace traits; update session to `current_step='knowledge_uploads'`, `steps_completed` to ≥3. Optional step per product note; skip maintains progress from previous step. [Assumption]
  - Idempotency: if an agent with same `(business_id, name)` exists, service may reuse it; names are not unique—use first creation in session scope. [Confirmed name duplicates allowed]

  4) attach_upload_links (Step 4)
  - Input: `{ businessId, userId, links: { vision?: string[], mission?: string[], catalog?: string[], faqs?: string[], kb?: string[], sops?: string[], tc?: string[] }, language? }`. [Confirmed concept; language Assumption]
  - Flow (single transaction per batch): for each URL list, create `knowledge_item` of type `url` with `display_name` derived from group label (e.g., “Vision”, “FAQs”) and dedupe by `(business_id, url)`; update session `current_step='completed'` when at least one knowledge item exists; set `steps_completed=4`. [Assumption]
  - Idempotency: re-sending same URL returns existing item; no duplicates. [Assumption consistent with repo]

  5) complete_registration
  - Input: `{ registrationId, userId }`. [Assumption]
  - Flow: if `business_id` linked, and either agent exists or at least one knowledge item exists (or explicit skip allowed), mark session completed; return progress snapshot from repositories. [Assumption]

- Invariants & Rules
  - Progress monotonicity: `steps_completed` cannot decrease; `current_step` can only advance in order: business_profile → agent_setup → knowledge_uploads → completed. [Assumption]
  - Tenancy boundary: after Step 2, every repository call must include `business_id` filter and cross-check membership. [Confirmed intent; membership check Assumption]
  - Data normalization: trim inputs, collapse repeated whitespace, strip trailing slashes from URLs, and standardize host casing. [Assumption]
  - TTL: any session with `expires_at <= now()` is invalid and cannot progress. [Confirmed concept]

**Data Contracts (JSON examples)**

- start_registration.request
  {
    "firstName": "Aisha",                         // [Confirmed]
    "email": "aisha@example.com",                // [Confirmed] regex: ^[^@\s]+@[^@\s]+\.[^@\s]+$
    "password": "hunter2!!"                       // [Confirmed] min 8; omitted when provider='google' [Assumption]
  }

- start_registration.response
  {
    "registrationId": "b6b7c2ce-2a04-4d24-9c24-2a0830b0c2a1",  // [Assumption]
    "user": {
      "id": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21",           // [Assumption]
      "email": "aisha@example.com",                           // [Confirmed]
      "firstName": "Aisha"                                    // [Confirmed]
    },
    "nextStep": "business"                                    // [Confirmed]
  }

- upsert_business_profile.request
  {
    "registrationId": "b6b7c2ce-2a04-4d24-9c24-2a0830b0c2a1",   // [Assumption]
    "userId": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21",           // [Assumption]
    "businessName": "Pocket AI Studio",                         // [Confirmed]
    "industry": "SaaS & Software",                              // [Confirmed]
    "specifyIndustry": "",                                      // [Confirmed]
    "lineOfBusiness": ["Customer Support", "Analytics"],        // [Confirmed]
    "lineOfBusinessCustom": [],                                   // [Confirmed]
    "country": "United States",                                 // [Confirmed]
    "website": "https://example.com"                            // [Confirmed]
  }

- upsert_business_profile.response
  {
    "business": {
      "id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",            // [Assumption]
      "name": "Pocket AI Studio",                               // [Confirmed]
      "industryCode": "industry:smb-software"                   // [Assumption mapping]
    },
    "niches": ["niche:customer-support", "niche:analytics"],     // [Assumption mapping]
    "session": {
      "id": "b6b7c2ce-2a04-4d24-9c24-2a0830b0c2a1",            // [Assumption]
      "currentStep": "agent_setup",                             // [Confirmed model step]
      "stepsCompleted": 2,                                       // [Assumption]
      "totalSteps": 4                                            // [Confirmed concept]
    }
  }

- configure_agent.request
  {
    "businessId": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",        // [Confirmed]
    "userId": "2c9f...",                                        // [Assumption]
    "agentName": "Pocket Assistant",                             // [Confirmed]
    "agentTitle": "Support Specialist",                          // [Confirmed label] -> role mapping [Assumption]
    "agentTone": "Friendly",                                     // [Confirmed label] -> enum mapping [Assumption]
    "agentTraits": ["Helpful", "Patient", "Proactive"],         // [Confirmed labels] -> enum mapping [Assumption]
    "agentEscalation": "On fallback"                              // [Assumption label] -> enum mapping
  }

- configure_agent.response
  {
    "agent": {
      "id": "3f981f0f-2f63-4ea4-9c7c-bb0d9d2e6c9a",              // [Assumption]
      "name": "Pocket Assistant",                                 // [Confirmed]
      "role": "support",                                          // [Assumption mapping]
      "tone": "friendly",                                         // [Assumption mapping]
      "traits": ["patient", "proactive"],                        // [Assumption mapping]
      "escalationRule": "on_fallback"                             // [Assumption mapping]
    },
    "session": {
      "currentStep": "knowledge_uploads",                         // [Confirmed model step]
      "stepsCompleted": 3                                          // [Assumption]
    }
  }

- attach_upload_links.request
  {
    "businessId": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",        // [Confirmed]
    "userId": "2c9f...",                                        // [Assumption]
    "links": {
      "vision": ["https://example.com/vision"],                  // [Confirmed]
      "faqs": ["https://example.com/faqs", "https://help.example.com"]
    },
    "language": "en"                                              // [Assumption]
  }

- attach_upload_links.response
  {
    "created": {
      "vision": 1,                                                 // [Assumption]
      "faqs": 2
    },
    "duplicates": 0,                                               // [Assumption]
    "session": {
      "currentStep": "completed",                                 // [Assumption]
      "stepsCompleted": 4                                          // [Assumption]
    }
  }

Notes
- All timestamps ISO8601 UTC; IDs are UUID v4. [Confirmed concept]
- Regex/ranges (enforced upstream or by repos): email format; URL must start with `https://`; file size ≤ 20MB; `industry_code` `^industry:[a-z0-9-]{2,50}$`; `niche_code` `^niche:[a-z0-9-]{2,50}$`. [Assumption]

**Tenancy & Permissions**

- Resolution of `business_id` [Confirmed intent]
  - Step 1: pre-tenant; service operates on `users` and `registration_sessions` via `user_id` + `registration_id` only. [Confirmed concept]
  - Step 2+: a `business_id` is created and attached to the session; all subsequent operations require `business_id`. [Confirmed intent]
- Role checks
  - Upon business creation, the service creates OWNER membership for `user_id`. Subsequent writes (agent, knowledge) require that the caller has OWNER/ADMIN membership. [Assumption]
- Cross-tenant risk mitigations
  - Every service method after Step 2 filters repo calls by `business_id` and verifies membership of `user_id`. [Assumption]

**Constraints & Quality Gates**

- Pagination defaults (when listing for any confirmations) remain `limit=20`, `max=100`. [Confirmed via repo]
- Timeouts: per-call DB statement timeout of 2s via repository base; service must not run long blocking logic in-transaction. [Confirmed via repo]
- Idempotency keys: (email); (registration_id)→single business; `(business_id,url)` for knowledge URLs; trait replacement set-equality. [Assumption]
- Progress computation: Step 2 = business exists; Step 3 = agent exists; Step 4 = any knowledge item exists. Persist `steps_completed` accordingly. [Assumption]
- Input caps: per `attach_upload_links`, up to 50 URLs per call (reject beyond) and max 500 total per business at registration time. [Assumption]
- Validation surface: `{ code, message, details? }` with codes `not_found`, `conflict`, `expired`, `validation`, `db_timeout`. [Confirmed]

**Dependencies & Stubs**

- Auth service (hashing, Google sign-in) and current-user context. [Confirmed need]
- Catalog mappers: UI label → `industry_code`/`niche_code`; `agentTitle`/`agentTone`/`agentTraits` → enums. [Assumption]
- URL normalization helper (strip/normalize). [Assumption]
- Transaction manager / Unit-of-Work to group repo calls per step. [Assumption]
- Storage adapters for files/text content are out-of-scope for current web flow (URLs only). [Confirmed]

**Risks & Tradeoffs**

- Mapping fidelity: UI labels may not cleanly map to enums/codes; requires a definitive mapping table to avoid `validation` errors. [Assumption]
- Duplicate business attempts: repeated Step 2 submissions could create conflicts; mitigated by transaction + `attach_business` conflict checks. [Confirmed behavior]
- Optional Step 3: allowing skip could result in `completed` with no agent; ensure downstream features tolerate that. [Assumption]
- Session TTL: users resuming after expiration produce `expired` errors; UX needs clear restart guidance. [Confirmed concept]
- URL dedupe without DB unique: race conditions mitigated with read-before-write; may still need DB unique in future. [Assumption]

**Acceptance Criteria**

- Service operations defined with inputs/outputs and error semantics for all steps. [Confirmed]
- Transactions and idempotency behaviors documented; progress invariants and step transitions specified. [Confirmed]
- Tenancy scoping by `business_id` post–Step 2 and membership checks outlined. [Confirmed]
- Dependencies (auth, mapping, UoW) enumerated with clear boundaries. [Confirmed]

**Open Questions**

- Final mapping table for: industry/LOB labels → codes; `agentTitle` → `AgentRole`; `agentTone` & `agentTraits` labels → enums; `agentEscalation` options → enum. Provide definitive lists. [Assumption] - CONFIRMED
- Should Step 4 accept files/texts in the web flow now, or URLs only? If files/texts are allowed, what per-call size/time limits apply? [Assumption] - DO THE MODERN AND THE STANDARD NEEDED
- Progress policy: should `completed` require both agent and at least one knowledge item, or either? Current spec says either is acceptable—confirm. [Assumption] - THE USER SHOULD BE ABLE TO REGISTER SKIPPING PARTS LIKE AGENT AND KNOWLEDGE, but the profile completion differs at this point because he hasnt completed all steps, but can get to the dashboard page without completing those mentioned two steps.
- Should `country` and `website` be persisted on `business` now or deferred to Settings? If persisted, to which fields/tables? [Assumption] - yes they should, probably the business info whatever table.
- What is the behavior for multiple active sessions for the same user? Prefer one active or allow multiple? [Assumption] - allow multiple
- Do we need auditing (who advanced step when) beyond `created_by_user_*` stored on business/agent/knowledge? [Assumption] yes we do.

