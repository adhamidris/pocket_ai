Cases App (apps/cases)
=====================

Purpose
-------
This app models customer cases and the service helpers used by the API and MCP
actions. It stores case metadata, history, messages, and linked documents.

Directory Map
-------------
- models.py
  Case, CaseHistoryEntry, CaseMessage, CaseDocumentLink, CaseNote.
- services.py
  Case CRUD helpers + serialization for API views.
- admin.py
  Admin configuration for case entities.

Key Flows
---------
1) Case creation
   MCP tools / API -> create_case(...) -> Case + CaseHistoryEntry.

2) Case updates
   update_case / update_case_status / add_case_history -> timeline updates.

3) Case messaging
   CaseMessage records AI/customer/system chat for auditability.

Configuration Touchpoints
-------------------------
- CasePriority / CaseStatus enums (models.py)

Quick Start (Dev)
----------------
- Create a case:
  `create_case(business_profile=..., payload=...)`
- Update status:
  `update_case_status(business_profile=..., case_id=..., status="closed")`

Examples
--------
Create case:
```python
from apps.cases.services import create_case
payload = {
  "title": "Refund request",
  "description": "Customer requested a refund for invoice 912...",
  "priority": "medium",
  "ai_diagnosis": "Billing dispute",
  "ai_actions_taken": "Captured request",
  "ai_suggested_actions": ["Verify invoice", "Process refund"],
  "metadata": {"source": "ai_orchestrator"},
}
create_case(business_profile=business, payload=payload)
```

Add history entry:
```python
from apps.cases.services import add_history_entry
add_history_entry(case=case, summary="Customer confirmed invoice ID", source="customer")
```

Troubleshooting
---------------
- Case not found:
  - Ensure case_id belongs to the business and status is not deleted.
- Missing messages:
  - Check CaseMessage creation in services.py.

Observability
-------------
- Case metrics: CaseMetrics in services.py (open_total, urgent_open, avg duration).
- API surfaces in apps/api/views.py.

Related Docs
------------
- `docs/architecture/llm_conversation_backend_flow.md`
- `docs/ops/manual_qa_playbook.md`

Glossary (Quick)
----------------
- Case: a tracked customer issue or request.
- Case history: short timeline entry for significant updates.
- Case message: full transcript snippet for auditability.

Where To Start (Reading Order)
------------------------------
1) `apps/cases/models.py`
2) `apps/cases/services.py`

High-Level Architecture
-----------------------
Conversations + MCP
   ↓
Cases (create/update)
   ↓
Admin/API views
