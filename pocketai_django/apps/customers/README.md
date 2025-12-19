Customers App (apps/customers)
==============================

Purpose
-------
This app manages customer records, contact points, notes, and activity
timelines for each business. It powers case linkage and customer profiles
in the dashboard.

Directory Map
-------------
- models.py
  Customer + contact points + notes + activity.
- services.py
  Customer list/detail helpers for API views.
- forms.py
  Admin-friendly forms for structured fields.

Key Flows
---------
1) Customer creation
   MCP tools or admin create/attach customer records.

2) Customer detail
   get_customer_detail(...) -> contacts, cases, notes, activity.

3) Placeholder customers
   CustomerManager.create_placeholder(...) for unknown identities.

Quick Start (Dev)
----------------
- List customers:
  `list_customers(business_profile=business)`
- Load detail:
  `get_customer_detail(business_profile=business, customer_id=...)`

Examples
--------
Create placeholder customer:
```python
customer = Customer.objects.create_placeholder(
    business_profile=business,
    label="Unknown Caller",
)
```

Fetch customer detail:
```python
detail = get_customer_detail(business_profile=business, customer_id=customer.id)
```

Troubleshooting
---------------
- Customer not found:
  - Ensure the customer belongs to the business profile.
- Missing contact points:
  - Check CustomerContactPoint entries and primary_email/phone.

Observability
-------------
- Customer stats: total_cases + open_cases (services.py).
- last_interaction_at updated via Customer.mark_interaction().

Related Docs
------------
- `docs/architecture/llm_conversation_backend_flow.md`
- `docs/ops/manual_qa_playbook.md`

Glossary (Quick)
----------------
- Customer: a business contact (may be placeholder).
- Contact point: email/phone/address/social link.
- Activity: timeline event (case update, note, etc.).

Where To Start (Reading Order)
------------------------------
1) `apps/customers/models.py`
2) `apps/customers/services.py`

High-Level Architecture
-----------------------
Customers
   ↓
Cases + Conversations
   ↓
Dashboard + API views
