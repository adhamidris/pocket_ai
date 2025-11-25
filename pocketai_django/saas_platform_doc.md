# AI Employee SaaS Platform - Technical Overview

**Document Purpose**: This document provides a comprehensive plain-English explanation of the platform architecture, features, and business model for context when building new features or integrations.

---

## Executive Summary

This is a B2B SaaS platform that provides businesses with AI-powered employees (agents) capable of handling customer support, sales, and CRM operations. The platform is built for global reach with a focus on affordable pricing for markets outside the US/EU tech bubble, particularly the Middle East, LATAM, and Asia.

**Core Value Proposition**: Any business can create a fully functional AI agent in under 3 minutes that can answer questions, create cases, book appointments, manage customer records, and escalate issues—all while maintaining high accuracy through RAG (Retrieval-Augmented Generation).

---

## Business Model Overview

### Target Market
- **Primary**: Small to medium businesses (solopreneurs to 50-person teams)
- **Industries**: Initially broad (e-commerce, service businesses, local shops, SaaS companies, healthcare, education)
- **Geographic Focus**: Middle East, LATAM, Asia (WhatsApp-first markets)
- **Current Phase**: Pre-launch, building toward first 100 beta customers

### Pricing Strategy (Proposed)
- **Tier 1 - Starter**: $29/month (500 conversations, 1 agent, basic features)
- **Tier 2 - Growth**: $99/month (2,500 conversations, 3 agents, WhatsApp + CRM)
- **Tier 3 - Business**: $299/month (10,000 conversations, unlimited agents, API access)
- **Overages**: ~$0.02 per conversation
- **Model**: Conversation-based pricing (not pay-as-you-go to avoid bill shock)

### Go-to-Market Strategy
1. **Phase 1**: Direct outreach to Instagram-based small businesses
2. **Phase 2**: Content-led viral marketing (comparison videos, case studies)
3. **Phase 3**: WhatsApp community marketing and word-of-mouth
4. **Built-in viral loop**: Every AI conversation ends with "Powered by [Brand]" footer

---

## Platform Architecture

### High-Level System Design

```
┌─────────────────────────────────────────────────────────────┐
│                     End User (Customer)                      │
│              (Person chatting with the AI)                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Chat Portal (Frontend)                    │
│  • Shareable link (current)                                  │
│  • Future: WhatsApp, Instagram DM, website widget            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              Backend Orchestration Layer (Django)            │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │   McpOrchestratorService (_execute_turn method)     │   │
│  │   • Manages conversation flow                        │   │
│  │   • Handles streaming decisions                      │   │
│  │   • Executes tool loops                              │   │
│  │   • Applies business guardrails                      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │   Knowledge/RAG System                               │   │
│  │   • Document ingestion pipeline                      │   │
│  │   • Vector embeddings                                │   │
│  │   • Semantic search                                  │   │
│  │   • Read-before-answer enforcement                   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │   Tool Execution System                              │   │
│  │   • search_knowledge                                 │   │
│  │   • read_document                                    │   │
│  │   • create_case                                      │   │
│  │   • create_customer                                  │   │
│  │   • book_appointment                                 │   │
│  │   • escalate_case                                    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │   Planner (Async Background Service)                 │   │
│  │   • Extracts actions/entities after response         │   │
│  │   • Categorizes cases                                │   │
│  │   • Detects sentiment/urgency                        │   │
│  │   • Runs after user sees the answer                  │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   LLM Provider (DeepSeek)                    │
│  • Current: DeepSeek for testing (cheap, fast)               │
│  • Future: OpenAI, self-hosted open source, or stick with DS│
│  • Uses MCP (Model Context Protocol) for tool calling        │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  Business Owner Dashboard                    │
│  • Analytics (conversation volume, response times)           │
│  • Customer records                                          │
│  • Case management                                           │
│  • Leads tracking                                            │
│  • Agent configuration                                       │
│  • Knowledge management                                      │
│  • Integrations (Google Drive, future: WhatsApp, Shopify)   │
└─────────────────────────────────────────────────────────────┘
```

---

## Core Features

### 1. Agent Creation (3-Minute Setup)
**User Flow**:
1. Sign up (email + password)
2. Create agent (name, industry, tone)
3. Upload knowledge (PDFs, CSVs, Word docs, Google Sheets)
4. Get shareable chat portal link
5. Paste link in social media bio, WhatsApp auto-reply, etc.

**Behind the Scenes**:
- Document ingestion pipeline processes uploads
- Smart scraping extracts structured data
- Vector embeddings created for semantic search
- Agent is immediately live and functional

### 2. Knowledge Management (RAG System)

**Supported Input Types**:
- **PDFs**: Product manuals, policies, guides
- **CSV**: Pricing tables, product catalogs, FAQs
- **Word Documents**: Standard business docs
- **Google Sheets**: Live inventory, pricing (future: real-time sync)
- **Future**: Website scraping, YouTube transcripts, email threads, social media posts, Shopify/WooCommerce catalogs

**Processing Pipeline**:
1. **Upload** → File stored and queued
2. **Extraction** → Text/tables extracted with smart parsing
3. **Chunking** → Content split into semantic chunks
4. **Embedding** → Vector embeddings generated
5. **Indexing** → Stored in vector database for search
6. **Status Tracking** → Dashboard shows processing status per document

**Search Behavior**:
- Semantic search across all uploaded knowledge
- Relevance scoring and ranking
- Citation tracking (AI references which documents it used)
- "Read-before-answer" enforcement for critical topics

### 3. Conversation Flow (How a Turn Works)

**Simple Question (No Tools Needed)**:
```
User: "What are your business hours?"
└─> LLM streams answer immediately
    └─> Single-pass streaming (1 LLM call)
        └─> Answer sanitized and shown to user
            └─> Planner runs in background (extracts metadata)
```

**Complex Question (Requires RAG)**:
```
User: "What's your refund policy for damaged items?"
└─> LLM decides it needs to search knowledge
    └─> Tool call: search_knowledge(query="refund policy damaged items")
        └─> Search returns relevant document chunks
            └─> LLM decides it needs full document context
                └─> Tool call: read_document(doc_id="policy_doc_123")
                    └─> Full document content retrieved
                        └─> LLM streams final answer with citations
                            └─> Planner runs in background
```

**Action Required (CRM Operation)**:
```
User: "I need to return my order #5678"
└─> LLM searches knowledge for return policy
    └─> LLM creates a case
        └─> Tool call: create_case(type="return", order_id="5678", ...)
            └─> Case record created in CRM
                └─> LLM streams confirmation with case number
                    └─> Business owner sees new case in dashboard
```

### 4. Tool Execution System

**Available Tools**:

| Tool Name | Purpose | Example Use |
|-----------|---------|-------------|
| `search_knowledge` | Find relevant documents/chunks | "Search for shipping policy" |
| `read_document` | Get full document content | "Read refund policy document" |
| `create_case` | Create support ticket | User reports issue |
| `create_customer` | Create customer record | New customer inquiry |
| `book_appointment` | Schedule appointment | "Book a consultation" |
| `escalate_case` | Flag for human review | Complex/angry customer |
| `update_case` | Modify existing case | Add notes, change status |
| `record_lead` | Save sales opportunity | Potential customer inquiry |

**Tool Loop Behavior**:
- LLM can call multiple tools in sequence (bounded by max iterations)
- Each tool execution updates internal context (ToolExecutionContext)
- Status indicators shown to user ("Searching...", "Reading document...")
- Tool results are invisible to end user (only final answer is shown)
- Tool trace logged for business owner analytics

### 5. Business Guardrails

**Identifier Gating**:
- Certain operations require customer identifiers (email, phone, order number)
- If missing, AI politely asks for them before proceeding
- Example: "To look up your order, I'll need your order number or email address."

**Read-Before-Answer Enforcement**:
- For sensitive topics (refunds, medical info, legal policies), AI must read full document before answering
- Prevents hallucination or partial information
- If document not fully read, AI shows placeholder and re-fetches

**PII Protection**:
- Customer emails, phone numbers, addresses are redacted from logs
- Tool errors sanitized before showing to users
- Internal system details never exposed

**Tenant Isolation**:
- Each business's knowledge is strictly isolated
- Cross-tenant data leakage prevented at database and search level

### 6. Streaming & Response Quality

**Current Implementation** (as of recent refactor):

**Streaming Strategy**:
- **Simple turns**: Single streaming LLM call with tools enabled
- **Tool turns**: Non-streaming tool loop + final streaming answer
- **Filler removal**: Phrases like "I'll check..." routed to status UI, not shown as text
- **Sanitization**: Investigative filler dropped, but conversational warmth kept
- **Fallback**: If sanitization removes everything, fall back to raw LLM text

**Planned Improvements** (from filter relaxation discussion):
- **Tier 1 Filters** (always drop): Tool names, internal syntax, PII, errors
- **Tier 2 Filters** (route to status): "I'll search...", "Let me check..."
- **Tier 3 Filters** (let through): "Great question!", "I'd be happy to help"
- **Config knob**: Per-agent tone control (Professional / Friendly / Free-flowing)

**Response Time Targets**:
- Simple FAQ: <2 seconds to first token
- Knowledge search: 3-5 seconds (acceptable for accuracy)
- CRM operations: 2-4 seconds (one-shot tool call)

### 7. CRM Dashboard

**Business Owner View**:

**Home Page (Analytics)**:
- Conversation volume (daily/weekly/monthly)
- Response times
- Top questions asked
- Customer satisfaction trends
- Agent performance metrics

**Customers Page**:
- Customer records (name, email, phone, history)
- Conversation history per customer
- Tags and segments
- Customer lifetime value tracking

**Cases Page**:
- All support tickets/cases
- Status (open, pending, resolved, escalated)
- Priority levels (low, medium, high, urgent)
- Assignment (AI-handled vs. needs human)
- Case notes and conversation context

**Leads Page**:
- Sales opportunities identified by AI
- Lead quality scoring
- Follow-up reminders
- Conversion tracking

**Agent Configuration Page**:
- Agent name, personality, tone
- Industry/vertical settings
- Response style (formal vs. casual)
- Custom instructions/prompts
- Feature toggles (which tools to enable)

**Knowledge Page**:
- Upload documents
- View processing status
- Edit/delete documents
- See which documents are most referenced
- **Future**: Knowledge gap detection ("10 customers asked about X, but no document covers it")

**Integrations Page**:
- Google Drive sync
- **Future**: WhatsApp Business API, Shopify, WooCommerce, Zapier, API access

---

## Technical Implementation Details

### Technology Stack
- **Backend**: Django (Python)
- **Database**: PostgreSQL (assumed, standard for Django SaaS)
- **Vector Database**: (Not specified, likely Pinecone, Weaviate, or pgvector)
- **LLM Provider**: DeepSeek (current), OpenAI (possible future)
- **Transport**: httpx with connection pooling
- **Streaming**: SSE (Server-Sent Events)
- **Background Tasks**: Async threading for planner

### Key Components

**McpOrchestratorService**:
- Central orchestration layer
- Lives in `apps/services/mcp/orchestrator.py`
- Main method: `_execute_turn` (handles entire conversation turn)
- Responsibilities:
  - Build prompts with budget constraints
  - Manage streaming vs. non-streaming LLM calls
  - Execute tool loops
  - Apply business guardrails
  - Coordinate with planner

**ToolExecutionContext**:
- Tracks state during a conversation turn
- Fields:
  - `knowledge_results`: Search results from RAG
  - `knowledge_reads`: Documents fully read
  - `tool_trace`: Log of all tool executions
  - `identifier_filters`: Required customer identifiers
  - `coverage_ledger`: Topics covered in conversation
  - Character budgets for rate limiting

**Streaming Pipeline**:
- Incoming SSE stream from LLM
- Delta assembly (chunk by chunk)
- Filler detection and filtering
- Sentence boundary detection
- Emission to frontend via callbacks
- Buffer management (prevents incomplete sentences)

**Document Ingestion Pipeline**:
- File upload handler
- Format-specific parsers (PDF, CSV, Word, Sheets)
- Text extraction and cleaning
- Semantic chunking
- Embedding generation
- Vector storage
- Status tracking and error handling

### Current Architecture Challenges

**Complexity in `_execute_turn`**:
- Single large method handling multiple concerns:
  - Prompt building
  - Streaming mechanics
  - Tool loop logic
  - Business rules
  - Provider quirks
- Shared mutable state across nested closures
- Hard to test individual pieces in isolation

**Planned Refactor** (from Codex analysis):
- Extract streaming logic into `StreamingEmitter` class
- Extract tool loop into `ToolLoopRunner`
- Extract policy gates into pure functions
- Introduce explicit data structures (StreamingResult, ToolLoopResult, TurnOutcome)
- Add targeted unit tests for each layer

**Note**: Refactor is not urgent; system works correctly as-is. Complexity is manageable technical debt, not blocking feature development.

---

## Integration Roadmap

### Current Integrations
- **Google Drive**: Import Google Sheets as knowledge
- **Chat Portal**: Shareable link (standalone web page)

### Planned Integrations (Priority Order)

**Phase 1 (Next 30 days)**:
1. **WhatsApp Business API** (via Twilio or WATI)
   - Legal, scalable solution
   - Conversation-based pricing (~$0.005-0.05 per conversation)
   - Webhook integration for incoming/outgoing messages
   - Status delivery receipts
   - Media support (images, voice notes)

2. **Website Scraper** (via Firecrawl or Jina AI)
   - Customer pastes website URL
   - Auto-crawl and ingest content
   - Reduces manual document upload

3. **"Powered By" Branding**
   - Footer in every conversation
   - Clickable link to signup page
   - Viral growth loop

**Phase 2 (60-90 days)**:
4. **Instagram DM Integration** (via Instagram Graph API)
   - Requires Facebook Business account
   - Responds to DMs automatically
   - Huge market in fashion, beauty, food businesses

5. **YouTube Video Ingestion**
   - Customer pastes YouTube link
   - Whisper API transcription
   - Embeds as searchable knowledge

6. **Multi-language Auto-Translation**
   - Single knowledge base
   - AI detects user language
   - Responds in Arabic, English, French, Spanish, etc.

7. **Voice Note Support** (WhatsApp focus)
   - Transcribe voice messages
   - AI responds in text or voice

**Phase 3 (6+ months)**:
8. **Shopify/WooCommerce Integration**
   - Auto-sync product catalog
   - Real-time inventory status
   - Order lookup and tracking

9. **Email Integration** (Gmail/Outlook)
   - Learn from past email conversations
   - Auto-respond to support emails
   - Thread tracking

10. **Proactive Outreach**
    - Abandoned cart recovery
    - Re-engagement campaigns
    - Follow-up reminders

11. **Sales Mode**
    - Lead qualification
    - Offer discounts based on rules
    - Close deals directly in chat

12. **API Access** (for developers)
    - Embed AI agent in custom apps
    - Webhook notifications
    - Programmatic knowledge upload

---

## Competitive Positioning

### Direct Competitors
- **Intercom, Zendesk**: Expensive ($400-800/month), complex setup, enterprise-focused
- **Chatbase, CustomGPT**: Simple but limited (no CRM, no actions)
- **Ada, Forethought**: AI-focused but enterprise-only, long sales cycles

### Competitive Advantages
1. **Price**: $29-299/month vs. $500-2000/month for competitors
2. **Speed**: 3-minute setup vs. weeks of implementation
3. **Geographic Focus**: Middle East, LATAM, Asia (underserved markets)
4. **WhatsApp-First**: Most competitors ignore WhatsApp (critical channel outside US/EU)
5. **CRM Built-In**: Not just chat, but case management, leads, appointments
6. **Multi-Language**: Easy translation for global businesses
7. **Vibe-Coded**: Fast iteration, customer-focused features, not enterprise bloat

### Market Positioning
- **Not competing on**: Enterprise features, complex workflows, on-premise deployment
- **Competing on**: Affordability, speed, ease of use, emerging market needs
- **Target customer**: Businesses that would otherwise hire a human support rep ($2-5K/month) or use no automation at all

---

## Key Design Decisions & Philosophy

### Why Streaming?
- **User expectation**: Modern AI feels responsive with token-by-token output
- **Perceived speed**: Even if total time is same, streaming feels faster
- **Feedback loop**: User sees AI is working, reduces abandonment

### Why Tool Loop (Not Single-Shot)?
- **Accuracy over speed**: Better to search → read → answer than guess
- **Auditability**: Business owner can see exactly what AI did (tool trace)
- **Safety**: Prevents hallucination by forcing grounding in documents

### Why Async Planner?
- **User experience**: Customer sees answer immediately
- **Background processing**: Extract metadata, categorize, analyze after the fact
- **Cost efficiency**: No need to wait for planner when user already has their answer

### Why Conversation-Based Pricing (Not Token-Based)?
- **Predictability**: Businesses hate surprise bills
- **Simplicity**: Easy to explain ("You get 500 conversations/month")
- **Fair**: Some conversations are short (1 message), some are long (10 messages)

### Why Knowledge Upload (Not Just Website Scraping)?
- **Quality control**: Businesses want to curate what AI knows
- **Private data**: Not everything is on their website (internal policies, pricing tiers)
- **Trust**: Explicit upload gives business owner control

### Why Not Voice-First?
- **Market reality**: Most businesses in target markets use text (WhatsApp, Instagram DM)
- **Complexity**: Voice adds latency, transcription errors, accent issues
- **Future plan**: Add voice as enhancement, not core feature

---

## Success Metrics (Post-Launch)

### Customer Acquisition
- Signups per week
- Activation rate (% who create an agent)
- Time to first conversation
- Viral coefficient (referrals per customer)

### Engagement
- Conversations per agent per day
- Average conversation length (messages)
- Tool usage rate (% of conversations using RAG)
- Customer retention (monthly churn)

### Quality
- Response accuracy (human review sample)
- Escalation rate (% needing human takeover)
- Customer satisfaction (post-chat survey)
- Knowledge gap detection (unanswered questions)

### Revenue
- MRR (Monthly Recurring Revenue)
- ARPU (Average Revenue Per User)
- LTV:CAC ratio (Lifetime Value : Customer Acquisition Cost)
- Expansion revenue (tier upgrades)

---

## Known Limitations & Future Improvements

### Current Limitations
1. **Single-channel**: Only shareable link (no WhatsApp, Instagram yet)
2. **English-focused**: Multi-language works but not optimized
3. **Manual knowledge upload**: No auto-sync from website/social media
4. **Limited analytics**: Basic metrics only, no deep insights
5. **No voice support**: Text-only conversations
6. **No API access**: Can't embed in custom apps yet

### Planned Improvements
1. **Multi-channel**: WhatsApp, Instagram, website widget, SMS
2. **Auto-knowledge sync**: Website scraping, social media, email threads
3. **Advanced analytics**: Sentiment trends, topic clustering, conversion funnels
4. **Voice support**: Transcription + text-to-speech
5. **API & webhooks**: Developer-friendly integrations
6. **White-label option**: Agencies can rebrand and resell
7. **Team collaboration**: Multiple users per business account
8. **Custom workflows**: If-then automation rules
9. **A/B testing**: Test different agent personalities/prompts
10. **Knowledge gap detection**: AI suggests missing content

---

## Developer Onboarding Guide

### If You're Building a New Feature

**Step 1: Understand the conversation flow**
- Read the "Conversation Flow" section above
- Trace a sample conversation through `_execute_turn`
- Identify which phase your feature touches (streaming, tools, planner)

**Step 2: Check existing patterns**
- Look for similar features already implemented
- Reuse existing tools and contexts where possible
- Follow established naming conventions

**Step 3: Consider the business owner experience**
- Will they see this in the dashboard?
- Does it need configuration options?
- Should it be logged for analytics?

**Step 4: Consider the end user experience**
- Is this visible in the chat?
- Does it add latency?
- Is it clear what's happening (status indicators)?

**Step 5: Think about edge cases**
- What if the LLM doesn't call your tool?
- What if the tool fails?
- What if the customer provides invalid input?

**Step 6: Add tests**
- Unit tests for pure logic
- Integration tests for tool execution
- Manual testing with real conversations

### If You're Fixing a Bug

**Step 1: Reproduce the issue**
- Get the exact conversation transcript
- Check logs for tool traces and errors
- Identify which phase failed (streaming, tools, planner)

**Step 2: Isolate the root cause**
- Is it the LLM's decision-making?
- Is it a tool execution error?
- Is it a business guardrail triggering incorrectly?
- Is it a frontend display issue?

**Step 3: Fix at the right level**
- **Prompt issue**: Adjust system prompts or examples
- **Tool issue**: Fix tool implementation or error handling
- **Guardrail issue**: Adjust policy logic
- **Display issue**: Fix frontend rendering

**Step 4: Prevent regression**
- Add a test case that catches this bug
- Update documentation if it revealed a misunderstanding
- Consider if similar bugs could exist elsewhere

---

## Glossary

- **Agent**: The AI employee created by a business owner
- **End User**: The customer chatting with the agent (not the business owner)
- **Conversation**: A series of messages between an end user and agent
- **Turn**: A single user message + agent response cycle
- **Tool**: A function the AI can call (search, read, create case, etc.)
- **Tool Loop**: Multiple tool executions in sequence before final answer
- **RAG**: Retrieval-Augmented Generation (search + read + answer)
- **Streaming**: Token-by-token output from LLM
- **Filler**: Investigative phrases like "I'll check..." that are routed to status
- **Sanitization**: Removing filler and internal narration from final answer
- **Planner**: Background service that extracts metadata after answer is shown
- **ToolExecutionContext**: State object tracking tools used in a turn
- **Identifier Gating**: Requiring customer email/phone before certain operations
- **Read-Before-Answer**: Forcing AI to read full document before answering
- **Tenant**: A business using the platform (synonymous with "customer" or "account")
- **Portal**: The chat interface where end users interact with the agent
- **MCP**: Model Context Protocol (tool calling standard)

---

## Questions to Ask When Planning New Features

1. **Does this help businesses make more money or save more time?**
2. **Can a non-technical business owner configure this themselves?**
3. **Does this work in WhatsApp? (If not, should it?)**
4. **Will this increase accuracy or just add complexity?**
5. **Can we ship an MVP version in <2 weeks?**
6. **Does this give us a competitive advantage or just match competitors?**
7. **Will customers pay more for this feature?**
8. **Does this improve the end user experience or just the dashboard?**
9. **Can we A/B test this before fully rolling out?**
10. **What's the fallback if this feature fails at runtime?**

---

## Final Notes for LLMs Reading This

**Context Window Considerations**:
- This platform handles long conversations (10+ turns)
- Each turn includes full conversation history + tool results
- Be mindful of token limits when building prompts

**Reliability Over Cleverness**:
- The AI should be helpful and accurate, not flashy
- Business owners care about: Does it work? Does it save me time?
- End users care about: Did I get my answer? Was it fast?

**This is Pre-Launch**:
- Features will change rapidly based on customer feedback
- Technical debt (like `_execute_turn` complexity) is acceptable for now
- Velocity matters more than perfect architecture at this stage

**The Founder's Philosophy**:
- "Vibe coding" = ship fast, iterate based on real usage
- Affordable pricing is a core value, not a race to the bottom
- Emerging markets (Middle East, LATAM, Asia) are underserved and ready for this

**When In Doubt**:
- Ask clarifying questions before building
- Bias toward simpler solutions
- Think about the business owner's perspective (they're not developers)
- Remember: This replaces hiring a human employee—that's the bar

---

**Document Version**: 1.0  
**Last Updated**: November 2024  
**Author**: Platform founder + Claude (Anthropic)  
**Intended Audience**: LLMs assisting with feature development, debugging, or strategic planning