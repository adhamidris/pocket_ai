# PocketAI SaaS — Business Brief

## One‑liner
PocketAI is a multi‑tenant B2B SaaS that lets any business launch an AI agent grounded in their own knowledge in minutes—then share it as a link or embed it to handle support and sales conversations at scale.

## Current Status (Beta)
- Web chat portal is live and in active use.
- Email connectors (Google/Microsoft) are live for search + draft workflows.
- Voice calling is implemented but currently **dev‑only**.
- Mobile app is paused; web portal is the primary channel.

## Problem
Businesses lose leads and overwhelm support teams when answers are scattered across PDFs, policies, spreadsheets, and internal docs. Hiring and training is expensive, and response times on social channels are often too slow.

## Solution
PocketAI gives each tenant a configurable AI agent that:
- Responds in the same language the end user uses (English/Arabic, etc.).
- Uses the tenant’s uploaded knowledge as the source of truth.
- Enforces privacy by default and only reveals sensitive information when the tenant enables verification and the conversation actually requires it.

## Target Customers
- SMBs and mid‑market teams across industries (general-purpose platform, not niche-specific).
- Initial geographic focus: MENA (e.g., Egypt, UAE, KSA, Jordan, Kuwait, Qatar).

## Core User Journey (Tenant)
1. Sign up and configure the tenant's main AI agent.
2. Create Workflow Agents for specialized repeatable tasks when deeper customization is needed.
3. Upload knowledge (PDF, DOCX, TXT/MD, scanned images/PDFs, CSV/XLSX, JSON).
4. (Optional) Connect business email (Gmail/Outlook) for search + draft workflows.
5. Publish the agent via a hosted portal link (and later: embedded widget / additional channels).
6. Monitor usage, quality, and operational metrics from the dashboard.

## End‑User Experience
- Fast “live” chat experience with immediate feedback and a complete answer after retrieval.
- Answers are grounded in tenant-provided knowledge; when information is missing, the agent asks the smallest clarifying question needed to proceed.

## Safety, Privacy, and Verification
- Default‑safe handling of sensitive data: the platform detects common PII fields and applies protective rules by default.
- Tenant-controlled verification: enable email/SMS/WhatsApp OTP flows when customer-specific data access is needed.
- Platform safety override: the system can refuse to disclose sensitive data even if a tenant misconfigures a knowledge source.

## Knowledge Lifecycle
- Tenants can overwrite documents as they update policies/pricing; the system reprocesses and uses the latest version for future answers.
- Deletion removes the document and its derived retrieval artifacts from the active system (indexes/caches) so it no longer influences responses.

## Auditability and Trust
- Access and activity audit logs (who accessed what and when) are retained for compliance and dispute resolution.
- Tenant isolation is foundational: each tenant’s knowledge and conversations are logically separated.

## Business Model (High-Level)
- Subscription-based plans with a free trial period.
- Plans can scale by usage (e.g., conversations), number of agents, enabled channels, and verification features.

## Positioning
PocketAI is the “fastest path to a business-ready AI agent”: self‑serve setup, Workflow Agent customization, privacy by default, and a focus on high-quality answers grounded in real business documents.
