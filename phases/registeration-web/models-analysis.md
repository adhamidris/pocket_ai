> Frontend Evidence Used

  - Step 1: First Name, Email, Password & Confirmation, Register with Google [Confirmed]
  - Step 2: Business Profile — Business Name, Industry (choices), Industry niches (dependent choices) [Confirmed]
  - Step 3: Agent Setup — Agent Name, Role (choices), Tone (choices), Traits (multi-select), Escalation Rule (choices) [Confirmed]
  - Step 4: Knowledge Uploads — choose doc type; upload fields populate per type (file/url/text; display name; language) [Confirmed]
  - Registration continuity via registration_id across steps [Assumption]
  - Multi-tenant scoping by business_id from Step 2 onward [Assumption]
  - Upload processing statuses for knowledge items (pending/processing/ready/failed) [Assumption]

  Phase Requirements

  - Goal: Define SQL data model to persist the 4-step registration flow artifacts and resulting tenant resources; detail tables, columns, PK/FK,
  unique/indexes, not-null, lifecycle.
  - Database: PostgreSQL (UUID PKs, TIMESTAMPTZ timestamps, ENUMs) [Assumption]
  - Conventions:
      - All rows include created_at TIMESTAMPTZ DEFAULT now() [Assumption]
      - Mutable rows include updated_at TIMESTAMPTZ with trigger to set now() on update [Assumption]
      - Multi-tenant rows include business_id (UUID) and are indexed by it [Assumption]

  Tables and Columns

  1. users — global identities (pre-tenant)

  - id: UUID PK [Assumption]
  - email: TEXT, NOT NULL, unique on lower(email) [Confirmed email, Assumption unique/normalize]
  - first_name: TEXT, NOT NULL (1–80) [Confirmed]
  - password_hash: TEXT, NULL (OAuth-only users have NULL) [Assumption]
  - auth_provider: TEXT, NOT NULL, DEFAULT 'password' ('google' for OAuth) [Assumption]
  - email_verified: BOOLEAN, NOT NULL, DEFAULT FALSE [Assumption]
  - created_at: TIMESTAMPTZ, NOT NULL, DEFAULT now() [Assumption]
    Indexes/constraints:
  - UNIQUE (lower(email)) [Assumption]
  - CHECK (char_length(first_name) BETWEEN 1 AND 80) [Assumption]

  2. registration_sessions — tracks the multi-step wizard state

  - id: UUID PK (registration_id) [Assumption]
  - user_id: UUID FK → users.id, NOT NULL [Assumption]
  - business_id: UUID FK → businesses.id, NULL (set after Step 2) [Assumption]
  - current_step: ENUM('business_profile','agent_setup','knowledge_uploads','completed'), NOT NULL [Assumption]
  - state: JSONB, NULL (ephemeral step data for recovery) [Assumption]
  - expires_at: TIMESTAMPTZ, NOT NULL (e.g., now()+7d) [Assumption]
  - created_at: TIMESTAMPTZ NOT NULL DEFAULT now() [Assumption]
  - updated_at: TIMESTAMPTZ NOT NULL DEFAULT now() [Assumption]
    Indexes/constraints:
  - INDEX ON (user_id) [Assumption]
  - CHECK (expires_at > created_at) [Assumption]
  - ON DELETE SET NULL for business_id, ON DELETE CASCADE for user_id [Assumption]

  3. businesses — tenant root

  - id: UUID PK [Assumption]
  - name: TEXT, NOT NULL (2–120) [Confirmed]
  - industry_code: TEXT, NOT NULL, format 'industry:...' [Confirmed]
  - created_by_user_id: UUID FK → users.id, NOT NULL [Assumption]
  - created_at: TIMESTAMPTZ NOT NULL DEFAULT now() [Assumption]
    Indexes/constraints:
  - INDEX (industry_code) [Assumption]
  - CHECK (char_length(name) BETWEEN 2 AND 120) [Assumption]
  - CHECK (industry_code ~ '^industry:[a-z0-9-]{2,50}$') [Assumption]

  4. business_niches — selected niches for a business

  - business_id: UUID FK → businesses.id, NOT NULL [Confirmed]
  - niche_code: TEXT, NOT NULL [Confirmed]
  - added_at: TIMESTAMPTZ NOT NULL DEFAULT now() [Assumption]
    PK/Indexes/constraints:
  - PK (business_id, niche_code) [Assumption]
  - CHECK (niche_code ~ '^niche:[a-z0-9-]{2,50}$') [Assumption]
  - ON DELETE CASCADE via business_id [Assumption]

  5. user_business_memberships — who belongs to a business (establishes tenancy roles)

  - user_id: UUID FK → users.id, NOT NULL [Assumption]
  - business_id: UUID FK → businesses.id, NOT NULL [Assumption]
  - role: ENUM('owner','admin','agent'), NOT NULL [Assumption]
  - joined_at: TIMESTAMPTZ NOT NULL DEFAULT now() [Assumption]
    PK/Indexes/constraints:
  - PK (user_id, business_id) [Assumption]
  - INDEX (business_id, role) [Assumption]
  - CHECK (role IN ('owner','admin','agent')) if not using ENUM [Assumption]
  - At registration: insert OWNER row for creator [Assumption]

  6. agents — configured virtual agent for the business

  - id: UUID PK [Assumption]
  - business_id: UUID FK → businesses.id, NOT NULL [Confirmed]
  - name: TEXT, NOT NULL (2–80) [Confirmed]
  - role: ENUM('sales','support','research','success','marketing'), NOT NULL [Assumption]
  - tone: ENUM('friendly','professional','casual','formal','empathetic','playful'), NOT NULL [Assumption]
  - escalation_rule: ENUM('never','on_fallback','on_negative_sentiment','on_high_value','always'), NOT NULL [Confirmed]
  - created_at: TIMESTAMPTZ NOT NULL DEFAULT now() [Assumption]
    Indexes/constraints:
  - UNIQUE (business_id, name) [Assumption]
  - CHECK (char_length(name) BETWEEN 2 AND 80) [Assumption]
  - INDEX (business_id) [Assumption]
  - ON DELETE CASCADE via business_id [Assumption]

  7. agent_traits — multi-select traits for an agent

  - agent_id: UUID FK → agents.id, NOT NULL [Confirmed]
  - trait_code: ENUM('concise','detailed','curious','patient','proactive','direct','creative'), NOT NULL [Confirmed]
  - added_at: TIMESTAMPTZ NOT NULL DEFAULT now() [Assumption]
    PK/Indexes/constraints:
  - PK (agent_id, trait_code) [Assumption]
  - ON DELETE CASCADE via agent_id [Assumption]

  8. knowledge_items — business knowledge sources

  - id: UUID PK [Assumption]
  - business_id: UUID FK → businesses.id, NOT NULL [Confirmed]
  - source_type: ENUM('file','url','text'), NOT NULL [Assumption]
  - status: ENUM('pending','processing','ready','failed'), NOT NULL DEFAULT 'pending' [Assumption]
  - display_name: TEXT, NULL (<=120) [Assumption]
  - language: TEXT, NULL (BCP-47) [Assumption]
  - created_by_user_id: UUID FK → users.id, NOT NULL [Assumption]
  - created_at: TIMESTAMPTZ NOT NULL DEFAULT now() [Assumption]
    Indexes/constraints:
  - INDEX (business_id, status) [Assumption]
  - CHECK (display_name IS NULL OR char_length(display_name) <= 120) [Assumption]
  - ON DELETE CASCADE via business_id [Assumption]

  9. knowledge_item_files — file-specific attributes (1:1 with knowledge_items)

  - knowledge_item_id: UUID PK, FK → knowledge_items.id, NOT NULL [Assumption]
  - filename: TEXT, NOT NULL (<=255) [Assumption]
  - content_type: TEXT, NULL (<=100) [Assumption]
  - storage_path: TEXT, NOT NULL (object storage key) [Assumption]
  - size_bytes: BIGINT, NOT NULL [Assumption]
  - checksum_sha256: TEXT, NULL [Assumption]
    Constraints:
  - CHECK (size_bytes BETWEEN 1 AND 20971520) — 20MB [Assumption]
  - ON DELETE CASCADE via knowledge_item_id [Assumption]

  10. knowledge_item_urls — url-specific attributes (1:1 with knowledge_items)

  - knowledge_item_id: UUID PK, FK → knowledge_items.id, NOT NULL [Assumption]
  - url: TEXT, NOT NULL [Confirmed]
    Constraints:
  - CHECK (url LIKE 'https://%') [Assumption]
  - ON DELETE CASCADE [Assumption]

  11. knowledge_item_texts — text-specific attributes (1:1 with knowledge_items)

  - knowledge_item_id: UUID PK, FK → knowledge_items.id, NOT NULL [Assumption]
  - text_content: TEXT, NOT NULL [Confirmed text source]
    Constraints:
  - CHECK (char_length(text_content) BETWEEN 1 AND 200000) [Assumption]
  - ON DELETE CASCADE [Assumption]

  Enums (DB-level)

  - agent_role_enum: sales, support, research, success, marketing [Assumption]
  - agent_tone_enum: friendly, professional, casual, formal, empathetic, playful [Assumption]
  - agent_trait_enum: concise, detailed, curious, patient, proactive, direct, creative [Confirmed traits concept; values Assumption]
  - escalation_rule_enum: never, on_fallback, on_negative_sentiment, on_high_value, always [Confirmed concept; values Assumption]
  - knowledge_source_enum: file, url, text [Assumption]
  - knowledge_status_enum: pending, processing, ready, failed [Assumption]
  - membership_role_enum: owner, admin, agent [Assumption]
  - registration_step_enum: business_profile, agent_setup, knowledge_uploads, completed [Assumption]

  Lifecycle Notes

  - Creating a business (Step 2) inserts businesses row, business_niches rows, and membership OWNER. [Assumption]
  - Agent setup (Step 3) inserts agents row + agent_traits rows. [Assumption]
  - Knowledge uploads (Step 4) insert knowledge_items row + exactly one of file/url/text child rows. [Assumption]
  - Deleting a business cascades to agents, agent_traits, knowledge_items, and their child rows. [Assumption]
  - registration_sessions expire via expires_at and are safe to delete without affecting created business resources (FKs set to NULL). [Assumption]

  Mermaid ER Diagram

  - erDiagram
      - USERS ||--o{ REGISTRATION_SESSIONS : has
      - USERS ||--o{ USER_BUSINESS_MEMBERSHIPS : joins
      - BUSINESSES ||--o{ USER_BUSINESS_MEMBERSHIPS : has
      - BUSINESSES ||--o{ BUSINESS_NICHES : has
      - BUSINESSES ||--o{ AGENTS : has
      - AGENTS ||--o{ AGENT_TRAITS : has
      - BUSINESSES ||--o{ KNOWLEDGE_ITEMS : has
      - KNOWLEDGE_ITEMS ||--|| KNOWLEDGE_ITEM_FILES : may_have
      - KNOWLEDGE_ITEMS ||--|| KNOWLEDGE_ITEM_URLS : may_have
      - KNOWLEDGE_ITEMS ||--|| KNOWLEDGE_ITEM_TEXTS : may_have

  Data Contracts (JSON examples)
  Notes: Values are illustrative; all timestamps are ISO8601 UTC.

  - users row [Confirmed vs Assumption tags per field]
    {
    "id": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21",            // [Assumption]
    "email": "aisha@example.com",                           // [Confirmed]
    "first_name": "Aisha",                                  // [Confirmed]
    "password_hash": "$argon2id$v=19$m=65536,...",          // [Assumption]
    "auth_provider": "password",                            // [Assumption]
    "email_verified": false,                                // [Assumption]
    "created_at": "2025-01-12T09:10:11Z"                    // [Assumption]
    }
  - registration_sessions row
    {
    "id": "b6b7c2ce-2a04-4d24-9c24-2a0830b0c2a1",           // [Assumption]
    "user_id": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21",      // [Assumption]
    "business_id": null,                                    // [Assumption]
    "current_step": "business_profile",                     // [Assumption]
    "state": null,                                          // [Assumption]
    "expires_at": "2025-01-19T09:10:11Z",                   // [Assumption]
    "created_at": "2025-01-12T09:10:11Z",                   // [Assumption]
    "updated_at": "2025-01-12T09:10:11Z"                    // [Assumption]
    }
  - businesses + business_niches rows
    {
    "id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",           // [Assumption]
    "name": "Pocket AI Studio",                             // [Confirmed]
    "industry_code": "industry:smb-software",               // [Confirmed]
    "created_by_user_id": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21", // [Assumption]
    "created_at": "2025-01-12T09:10:11Z"                    // [Assumption]
    }
    {
    "business_id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",  // [Assumption]
    "niche_code": "niche:customer-support",                 // [Confirmed]
    "added_at": "2025-01-12T09:10:11Z"                      // [Assumption]
    }
  - agents + agent_traits rows
    {
    "id": "3f981f0f-2f63-4ea4-9c7c-bb0d9d2e6c9a",           // [Assumption]
    "business_id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",  // [Confirmed]
    "name": "Pocket Assistant",                             // [Confirmed]
    "role": "support",                                      // [Assumption]
    "tone": "friendly",                                     // [Assumption]
    "escalation_rule": "on_fallback",                       // [Confirmed]
    "created_at": "2025-01-12T09:10:11Z"                    // [Assumption]
    }
    {
    "agent_id": "3f981f0f-2f63-4ea4-9c7c-bb0d9d2e6c9a",     // [Assumption]
    "trait_code": "empathetic",                             // [Confirmed]
    "added_at": "2025-01-12T09:10:11Z"                      // [Assumption]
    }
  - knowledge_items (+ child, file example)
    {
    "id": "b35dbf81-ea48-4cf8-9e0b-1a38f4b8a3ed",           // [Assumption]
    "business_id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7",  // [Confirmed]
    "source_type": "file",                                  // [Assumption]
    "status": "processing",                                 // [Assumption]
    "display_name": "Product FAQ",                          // [Assumption]
    "language": "en",                                       // [Assumption]
    "created_by_user_id": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21", // [Assumption]
    "created_at": "2025-01-12T09:10:11Z"                    // [Assumption]
    }
    {
    "knowledge_item_id": "b35dbf81-ea48-4cf8-9e0b-1a38f4b8a3ed", // [Assumption]
    "filename": "faq.pdf",                                 // [Assumption]
    "content_type": "application/pdf",                     // [Assumption]
    "storage_path": "tenant/caa65c6c/docs/faq.pdf",        // [Assumption]
    "size_bytes": 1048576,                                 // [Assumption]
    "checksum_sha256": "..."                               // [Assumption]
    }
  - knowledge_items (+ child, url example)
    {
    "id": "f5b1d6ad-7ef8-4198-8ea8-9cb7a7f52d9e",          // [Assumption]
    "business_id": "caa65c6c-3630-4c3f-88a7-9113e6c8e6d7", // [Confirmed]
    "source_type": "url",                                  // [Assumption]
    "status": "pending",                                   // [Assumption]
    "display_name": "Help Center",                         // [Assumption]
    "language": "en",                                      // [Assumption]
    "created_by_user_id": "2c9f7f4e-3d62-4a9b-8b3c-2f7b034b0a21", // [Assumption]
    "created_at": "2025-01-12T09:10:11Z"                   // [Assumption]
    }
    {
    "knowledge_item_id": "f5b1d6ad-7ef8-4198-8ea8-9cb7a7f52d9e", // [Assumption]
    "url": "https://example.com/docs/getting-started"      // [Confirmed]
    }

  Tenancy & Permissions

  - Tenancy anchor: business_id on businesses, agents, agent_traits, knowledge_items, and child tables. All queries in non-registration contexts
  filter by business_id. [Assumption]
  - User accounts are global; memberships table grants roles per business: OWNER/ADMIN/AGENT. [Assumption]
  - Registration sessions are user-scoped and may optionally reference a business after Step 2; do not convey tenant access by themselves.
  [Assumption]
  - Cross-tenant safeguards: all agent/knowledge operations require business_id FK; cascade deletes on business prevent orphaned tenant data.
  [Assumption]

  Constraints & Quality Gates

  - Lengths: first_name 1–80; business_name 2–120; display_name ≤120; file name ≤255; content_type ≤100. [Assumption]
  - Formats: industry_code and niche_code regex as above; URL must start with https://; language BCP‑47 string (not enforced by DB beyond length).
  [Assumption]
  - Sizes: file size_bytes ≤ 20MB; text_content length ≤ 200k chars (DB CHECK). [Assumption]
  - Uniqueness: users.unique lower(email); agents.unique (business_id, name); business_niches.unique (business_id, niche_code); memberships.unique
  (user_id, business_id). [Assumption]
  - Indexes: business_id indexes on all tenant tables; status index on knowledge_items; role index on memberships. [Assumption]
  - Timestamps: created_at/updated_at TIMESTAMPTZ; updated_at maintained via trigger. [Assumption]
  - Expiry: registration_sessions.expires_at for cleanup (e.g., job purges expired sessions). [Assumption]

  Dependencies & Stubs

  - Industry/niche catalogs may remain as static JSON; if not persisted, business_niches still records selected codes. [Assumption]
  - Object storage integration provides storage_path and checksum for files. [Assumption]
  - Ingestion pipeline updates knowledge_items.status asynchronously. [Assumption]
  - Identity provider integration for Google accounts; password hashing handled elsewhere. [Assumption]

  Risks & Tradeoffs

  - Normalization vs. simplicity: separate child tables for knowledge sources avoid sparse nullable columns but add joins. [Assumption]
  - Enum evolution: changing agent/knowledge enums requires migrations; ensure forward compatibility strategy. [Assumption]
  - OAuth duplicates: uniqueness on email may block linking if password account exists; requires service-layer resolution strategy. [Assumption]
  - Registration recovery: storing state JSON in registration_sessions may tempt logic creep; keep minimal to avoid coupling. [Assumption]
  - Cascade deletes: powerful but dangerous; ensure business deletion is guarded at service layer. [Assumption]

  Acceptance Criteria

  - Tables, columns, PK/FK, NOT NULL, CHECK, UNIQUE, and indexes are fully specified for: users, registration_sessions, businesses, business_niches,
  user_business_memberships, agents, agent_traits, knowledge_items and its three child tables.
  - All tenant data includes business_id and has an index on it.
  - Enum sets for agent/knowledge/membership/step are defined with initial values and referenced by tables.
  - Lifecycle and cascading behaviors are documented (especially for business and knowledge items).
  - Migration notes include creation of enums, triggers for updated_at, and required indexes.

  Open Questions

  - Should we persist industries/niches as DB reference tables or continue as static catalogs? If persisted, confirm labels and codes source of truth.
  [Needed] --> Answer: DB reference tables, label 1 "Industry" label 1 data; [(E-commerce, e-commerce, retail)
(SaaS, saas, software)
(Finance, financial)
(Healthcare, health)
(Education, education)
(Hospitality, hospitality, travel)
(Manufacturing, manufactur)
(Logistics, logistics, transport)
(Real Estate, real estate)
(Media & Entertainment, media, entertainment)
(Telecommunications, telecom)
(Energy & Utilities, energy, utilit)
(Nonprofit & NGOs, nonprofit, ngo)
(Professional Services, professional)
(Consumer Services, consumer)
(Other)] -- Label 2 "Industry niches" label 2 data; [E-commerce:
(Apparel, Electronics, Beauty & Personal Care, Home & Kitchen, Sports & Outdoors, Groceries, Digital Goods, Handmade & Crafts, Automotive Accessories)

SaaS:
(CRM, Marketing Automation, Analytics, Project Management, Customer Support, Developer Tools, Productivity, Security, Billing/Subscriptions, Auth/Identity, Observability, Data Platform)

Finance:
(Banking, Lending, Payments, Wealth Management, Insurance, Accounting, Crypto/Blockchain, Trading Platforms)

Healthcare:
(Clinics, Telemedicine, Pharmacy, Diagnostics, Medical Devices, Wellness, Electronic Health Records)

Education:
(K-12, Higher Education, EdTech Platform, Corporate Training, Test Prep, Language Learning, Tutoring & Coaching)

Hospitality:
(Hotels, Restaurants, Catering, Travel & Tours, Venues & Events, Short-Term Rentals)

Manufacturing:
(OEM Production, Contract Manufacturing, CNC Machining, Injection Molding, 3D Printing, PCB Assembly, Quality Assurance, Procurement & Supply, Packaging, Maintenance (MRO))

Logistics:
(Freight Forwarding, Last-Mile Delivery, Warehousing & Fulfillment, Cold Chain, Customs Brokerage, Fleet Management, Courier, LTL/FTL Trucking, Air Cargo, Ocean Freight)

Real Estate:
(Residential Sales, Commercial Leasing, Property Management, Valuation & Appraisal, Real Estate Development, Facility Management, Co-working, Mortgage Brokerage, Title & Escrow, Short-Term Rentals)

Media & Entertainment:
(Streaming Subscriptions, OTT Platform, Content Production, Post-Production, Music Publishing, Game Development, Live Events, Digital Advertising, Influencer Campaigns, Licensing & Syndication)

Telecommunications:
(Mobile Voice, Fixed Broadband, VoIP, IoT Connectivity, Cloud PBX, SIP Trunking, Managed Networks, 5G Solutions, Fiber to the Home, Data Center Colocation)

Energy & Utilities:
(Electricity Supply, Natural Gas Supply, Renewable Generation, Solar Installation, Energy Storage, Smart Metering, Demand Response, Energy Trading, EV Charging, Utility Billing)

Nonprofit & NGOs:
(Fundraising, Grant Management, Program Delivery, Volunteer Management, Advocacy & Outreach, Education Programs, Healthcare Missions, Disaster Relief, Community Development, Monitoring & Evaluation)

Professional Services:
(Consulting, Legal Advisory, Tax & Audit, Accounting, Architecture, Engineering, Design & Creative, Recruitment, IT Consulting, Managed IT)

Consumer Services:
(Home Cleaning, Appliance Repair, Beauty & Wellness, Fitness & Training, Tutoring, Pet Care, Event Planning, Photography, Home Renovation, Moving & Storage)

Other:
(Consulting, Custom Development, Training & Enablement, Support & Success)]
  - Final enum values for role, tone, traits, escalation_rule — any additions/removals? [Needed] - no additions nor removals needed. confirmed.
  - Should agents be unique by (business_id, name) or allow duplicates? [Needed] - allow duplicates.
  - Registration session TTL (e.g., 7 days) and allowed recovery behaviors? [Needed] -- yes confirmed.
  - Do we store any audit fields (created_by for agents/knowledge items) beyond created_by_user_id? [Needed] yes created by user name as an addition
  - Any need to support multiple agents per business at registration, or is exactly one required? [Needed] -- no need for more than one agent on registeration process, and have it non mandatory as well to finalize a registeration processs.
  - File size cap and supported mime types definitive list at DB level or service-only? [Needed] -- not sure what to choose but you do recommend. accepted types pdf, word related, excel sheets and google sheets. 


-- FOR THE REGISTERATION we need to have a steps completion counter so we can use it later to inform customers that their profile is ~% complete or incomplete.