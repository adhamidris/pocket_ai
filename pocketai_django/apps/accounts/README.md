Accounts App (apps/accounts)
===========================

Purpose
-------
This app owns core tenant identity, onboarding, and configuration:
users, businesses, agents, knowledge uploads, integrations, and feature flags.
It is the backbone for multi-tenant configuration and access control.

Directory Map
-------------
- models.py
  User, BusinessProfile, AgentProfile, KnowledgeUpload, and integrations.
- registration.py
  Registration wizard steps and validation.
- agents.py
  Agent listing + detail helpers and labels.
- action_controls.py
  Per-agent action permission toggles for active non-retired actions.
- feature_flags.py
  Business-level feature flag helpers.
- credential_secrets.py
  Credential storage policies and validation.
- admin.py
  Admin site configuration.
- management/
  Operational commands (ingestion, eval, backfills).
- urls.py / views.py
  Thin HTTP entry points (used by apps/api).

Key Flows
---------
1) Registration wizard
   start_registration -> upsert_business_profile -> configure_agent_profile
   -> finalize_knowledge_uploads.

2) Agent configuration
   Agents are tied 1:1 to a business; action permissions and tone are stored
   here and read by MCP + RAG.

3) Knowledge uploads
   Upload metadata lives here; ingestion pipeline in apps/knowledge reads it.

Configuration Touchpoints
-------------------------
- FEATURE_FLAG_METADATA_KEY (business flags payload)
- BusinessProfile.metadata (feature flags, privacy policies)
- AgentProfile.tone and default assistant tool settings
- BusinessProfile.table_privacy_policy() (tabular masking rules)

Quick Start (Dev)
----------------
- Create user + business:
  `start_registration(...)` + `upsert_business_profile(...)`
- Configure agent:
  `configure_agent_profile(...)`
- Toggle an action:
  `set_action_setting(agent, action_key="read_knowledge", enabled=True)`

Security Notes
--------------
- Credentials for integrations are stored via credential_secrets.py policies.
- KnowledgeUpload visibility controls what RAG/MCP can read.

Examples
--------
Create a registration session:
```python
result = start_registration(first_name="Ada", email="ada@example.com", password="pass1234")
```

Update feature flags:
```python
FeatureFlagService.set_flags(business, updates={"hybrid_search": True})
```

Resolve an agent label:
```python
display_role_label("support")  # -> "Support Agent"
```

Toggle action permissions:
```python
set_action_setting(agent, action_key="read_knowledge", enabled=False)
```

Create a knowledge upload record:
```python
KnowledgeUpload.objects.create(
    business_profile=business,
    user=business.user,
    source_type="file",
    status="active",
    display_name="Policies",
)
```

Troubleshooting
---------------
- Business slug collisions:
  - BusinessProfile.ensure_slug() auto-resolves, check slug field.
- Agent not found:
  - Ensure agent_profile exists and status is active.
- Feature flags missing:
  - BusinessProfile.ensure_feature_flags() runs on save.

Observability
-------------
- Key models: BusinessProfile, AgentProfile, KnowledgeUpload.
- RegistrationSession tracks onboarding state and timestamps.

Related Docs
------------
- `docs/architecture/llm_conversation_backend_flow.md`
- `docs/ops/manual_qa_playbook.md`

Glossary (Quick)
----------------
- BusinessProfile: tenant record.
- AgentProfile: chat agent config for a tenant.
- KnowledgeUpload: source metadata for ingestion.

Where To Start (Reading Order)
------------------------------
1) `apps/accounts/models.py`
2) `apps/accounts/registration.py`
3) `apps/accounts/agents.py`
4) `apps/accounts/feature_flags.py`

High-Level Architecture
-----------------------
Accounts (tenant config)
   ↓
Knowledge + RAG + MCP
   ↓
LLM responses
