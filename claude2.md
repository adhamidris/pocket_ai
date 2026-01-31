# AI-Powered Phone Call Feature - Implementation Plan

## Overview

Add AI-powered outbound phone calling capabilities to the platform, enabling orchestrators and sub-agents to conduct real-time voice conversations with customers. Built on Twilio (telephony), Deepgram (STT), and ElevenLabs (TTS) with <800ms latency target.

## Technical Stack

- **Telephony**: Twilio Voice API + Media Streams
- **Speech-to-Text**: Deepgram (streaming, real-time)
- **Text-to-Speech**: ElevenLabs (premium quality)
- **Per-workspace**: Dedicated phone numbers via Twilio
- **Scope**: Outbound calls only, domestic (v1)
- **Languages**: English first (Arabic in future phase)

## Architecture Summary

### Core Flow

```
Agent/Sub-Agent initiates_phone_call tool
  ↓
CallSession created (QUEUED status)
  ↓
voice_call_worker claims job (database queue)
  ↓
Gather pre-call context (memory + knowledge)
  ↓
Twilio initiates call → Customer answers
  ↓
WebSocket: Twilio ←→ Deepgram ←→ LLM ←→ ElevenLabs
  ↓
Transcript stored as ConversationMessages
  ↓
Post-call: summary, action items, memory extraction
```

### Integration Points with Existing System

1. **Agent System**: CallSession links to AgentProfile + optional AgentRun (follows AgentRun pattern)
2. **Memory**: Transcripts stored as ConversationMessages in execution_conversation
3. **Background Jobs**: New voice_call_worker follows knowledge_ingestion_worker pattern (database-backed queue, no Celery)
4. **Real-time**: SSE for call events (follows chat_portal.py pattern)
5. **Cost Tracking**: Per-call cost records linked to BusinessProfile

## Database Schema

### New Django App: `apps/voice/`

#### Core Models

**VoicePhoneNumber**: Per-workspace Twilio phone numbers
- Links to BusinessProfile
- Stores Twilio SID, phone number (E.164), status
- Monthly cost tracking

**CallSession**: Single phone call instance
- Links to: BusinessProfile, AgentProfile, VoicePhoneNumber
- Optional links: AgentRun (if triggered by sub-agent), Conversation (anchor), execution_conversation (transcript storage)
- Pre-call: objective, context_snapshot, customer_snapshot
- Runtime: Twilio call SID, status, language, voice_id
- Post-call: summary, action_items, outcome
- Cost tracking: Twilio, Deepgram, ElevenLabs, LLM costs
- Worker coordination: lease_expires_at, attempt_count (follows AgentRun pattern)

**CallEvent**: Append-only event log
- Types: initiated, ringing, answered, consent_requested, transcript, llm_response, tts_started, ended, error
- Sequence ordering for deterministic playback
- Speaker tracking (customer, agent, system)
- Latency metrics

**CallTranscriptSegment**: Finalized transcript with speaker diarization
- Segment index ordering
- Start/end timestamps (milliseconds)
- Language, confidence scores

**CallContextBucket**: Dynamic context injection
- Sources: pre_call, live_retrieval, memory, knowledge
- Priority-based ordering
- Trigger phrase tracking
- Expiration timestamps

**VoiceCostRecord**: Detailed billing breakdown
- Per-service costs (Twilio, Deepgram, ElevenLabs, LLM)
- Quantity + unit tracking (minutes, characters, tokens)

**VoiceCallAuditEvent**: Compliance audit trail
- Links to actor agent
- Action types: initiated, consent_recorded, transcript_stored, ended

**VoiceConfiguration**: Per-workspace settings
- Monthly budget, concurrent call limits
- Default voices (English/Arabic)
- Consent message templates
- Alert thresholds

## Implementation Components

### 1. Background Worker (`apps/voice/management/commands/voice_call_worker.py`)

**Pattern**: Follows `process_knowledge_ingestion.py` and `process_agent_runs.py`

**Job Claiming**:
```python
# Atomic claim with SELECT FOR UPDATE SKIP LOCKED
candidate = CallSession.objects.select_for_update(skip_locked=True).filter(
    status=CallSessionStatus.QUEUED,
    attempt_count__lt=F("max_attempts")
).order_by("queued_at").first()
```

**Processing**:
1. Claim job (update status to INITIATING, set lease)
2. Gather pre-call context from memory/knowledge
3. Initiate Twilio call (returns call SID)
4. Real-time processing handled via Twilio webhooks
5. Post-call: transcript finalization, summary generation, cost calculation

### 2. Real-Time Pipeline (`apps/voice/twilio_stream.py`)

**TwilioMediaStreamHandler** (async/asyncio):
- Handles WebSocket connection from Twilio
- Concurrent tasks:
  - Inbound audio → Deepgram STT
  - Customer transcript → LLM response generation
  - LLM response → ElevenLabs TTS
  - Outbound audio → Twilio → Customer

**DeepgramStreamingClient** (`apps/voice/deepgram_client.py`):
- WebSocket connection to Deepgram API
- Model: nova-2 (fastest, most accurate)
- Features: interim results, utterance end detection, VAD events
- Endpointing: 300ms silence detection

**ElevenLabsStreamingClient** (`apps/voice/elevenlabs_client.py`):
- WebSocket streaming API for minimal latency
- Output format: ulaw_8000 (Twilio-compatible)
- Text chunking for progressive synthesis

**CallContextManager** (`apps/voice/context_manager.py`):
- Manages CallContextBucket entries
- Priority-based context injection into LLM prompts
- Token budget enforcement

### 3. MCP Tool Integration (`apps/mcp/tools.py`)

**New Tool: `initiate_phone_call`**

Parameters:
- `phone_number` (required): E.164 format
- `objective` (required): Clear call purpose
- `customer_id` (optional): For context pre-loading
- `language` (default: "en"): en or ar
- `context_items` (optional): Additional context
- `max_duration_minutes` (default: 10)

**Flow**:
1. Validate phone number format
2. Check workspace voice_config (enabled, budget)
3. Get workspace phone number
4. Pre-gather context from memory/knowledge
5. Create CallSession (status: QUEUED)
6. Return call_session_id

**Additional Tools**:
- `check_call_status`: Get current call status
- `get_call_summary`: Retrieve summary + transcript

### 4. API Endpoints

**REST** (`apps/api/voice_calls.py`):
- `POST /api/voice/initiate`: Initiate call (mirrors tool)
- `GET /api/voice/calls/{id}/status`: Call status
- `GET /api/voice/calls/{id}/transcript`: Full transcript
- `GET /api/voice/calls`: Call history
- `GET /api/voice/costs`: Cost report

**Twilio Webhooks** (`apps/voice/twilio_webhooks.py`):
- `POST /voice/webhook`: TwiML generation for call flow
- `WS /voice/stream`: Media Streams WebSocket endpoint
- `POST /voice/consent/{id}`: Consent response handler

**SSE** (`apps/api/voice_events.py`):
- `GET /api/voice/calls/{id}/events`: Real-time event stream
- Follows `chat_portal.py` SSE pattern
- Event types: callEvent, callEnded, heartbeat

### 5. Memory Integration (`apps/voice/call_processing.py`)

**Post-call finalization**:

1. **Store transcript as ConversationMessages**:
   - Create/reuse execution_conversation (follows AgentRun pattern)
   - Each CallTranscriptSegment → ConversationMessage
   - Sender: CUSTOMER or AI
   - Metadata: source="voice_transcript", timestamps, language

2. **Extract structured memory**:
   - Use LLM to extract facts, decisions, follow-ups
   - Create AgentRunMemoryItem entries (if linked to AgentRun)
   - Kinds: FACT, DECISION, EXTRACTED_DATA

3. **Queue compaction job**:
   - Create ConversationMaintenanceJob (kind: COMPACT_HISTORY)
   - Existing conversation worker handles embedding generation

### 6. Cost Management (`apps/voice/cost_service.py`)

**Cost Rates** (configurable):
- Twilio: ~$0.015/min (domestic outbound)
- Deepgram: ~$0.0043/min
- ElevenLabs: ~$0.30/1000 chars
- LLM: Variable (Claude Sonnet ~$3 input, $15 output per 1M tokens)

**Budget Enforcement**:
- Check before initiating call
- Monthly budget tracking per BusinessProfile
- Alert at threshold (default: 80%)
- Hard stop if budget exceeded

**Cost Recording**:
- Per-service VoiceCostRecord entries
- Aggregate totals on CallSession
- Monthly rollup for reporting

### 7. Compliance

**Consent Handling**:
- Play consent message at call start (configurable per language)
- Gather response via DTMF (press 1 to continue)
- Record consent decision in CallSession.consent_obtained
- Audit event for all consent actions

**Retention**:
- Follow existing TenantMemoryConfiguration policies
- Transcripts subject to hot/warm/archive/purge tiers
- Legal hold support (skip purge if enabled)

**Audit Trail**:
- VoiceCallAuditEvent for all call lifecycle events
- Actor agent tracking
- Immutable event log

## Critical Files

### New Files to Create

1. **Models**:
   - `apps/voice/models.py` - All voice call models
   - `apps/voice/migrations/0001_initial.py` - Schema migration

2. **Workers**:
   - `apps/voice/management/commands/voice_call_worker.py` - Main call processor
   - `apps/voice/call_processing.py` - Worker service (job claiming, execution)

3. **Real-time Pipeline**:
   - `apps/voice/twilio_stream.py` - WebSocket handler for Twilio Media Streams
   - `apps/voice/deepgram_client.py` - Deepgram streaming client
   - `apps/voice/elevenlabs_client.py` - ElevenLabs streaming client
   - `apps/voice/context_manager.py` - Context bucket management

4. **Services**:
   - `apps/voice/cost_service.py` - Cost tracking and budget enforcement
   - `apps/voice/consent.py` - Consent handling

5. **API**:
   - `apps/api/voice_calls.py` - REST endpoints
   - `apps/api/voice_events.py` - SSE event streaming
   - `apps/voice/twilio_webhooks.py` - Twilio webhook handlers

6. **Integration**:
   - Modify `apps/mcp/tools.py` - Add initiate_phone_call tool
   - Modify `apps/mcp/tools.py` - Add TOOL_DEFINITIONS entries

7. **Configuration**:
   - `apps/voice/__init__.py`, `apps.py` - App configuration
   - Modify `pocketai/settings.py` - Add voice settings

### Files to Reference (Existing Patterns)

- `apps/conversations/models.py` - AgentRun, AgentRunEvent patterns
- `apps/accounts/models.py` - AgentProfile, BusinessProfile integration
- `apps/accounts/management/commands/knowledge_ingestion_worker.py` - Worker pattern
- `apps/conversations/management/commands/process_agent_runs.py` - Worker pattern
- `apps/api/chat_portal.py` - SSE streaming pattern
- `apps/rag/ai_orchestrator.py` - KnowledgeSearchService for retrieval
- `apps/conversations/memory_extraction.py` - Memory extraction patterns

## Phase 1 MVP Scope

### Included

- [x] Outbound calls only
- [x] English language only
- [x] Basic call initiation via tool
- [x] Real-time STT/TTS pipeline
- [x] Consent message and recording
- [x] Transcript storage as ConversationMessages
- [x] Post-call summary generation
- [x] Cost tracking and budget enforcement
- [x] SSE event streaming
- [x] Basic audit trail
- [x] Pre-call context gathering (static snapshot)

### Deferred to Phase 2

- [ ] Arabic language support
- [ ] Background retrieval agent (live context injection)
- [ ] Inbound call support
- [ ] Call transfer to human
- [ ] Advanced voice cloning
- [ ] Call recording storage (S3/media files)
- [ ] SMS integration
- [ ] Advanced analytics dashboard

## Settings (Environment Variables)

```bash
# Twilio
TWILIO_ACCOUNT_SID=<account_sid>
TWILIO_AUTH_TOKEN=<auth_token>
TWILIO_WEBHOOK_BASE_URL=https://yourdomain.com

# Deepgram
DEEPGRAM_API_KEY=<api_key>
DEEPGRAM_MODEL=nova-2

# ElevenLabs
ELEVENLABS_API_KEY=<api_key>
ELEVENLABS_DEFAULT_VOICE_EN=<voice_id>

# Voice Processing
VOICE_MAX_CALL_DURATION_SECONDS=1800
VOICE_CONTEXT_BUCKET_MAX_TOKENS=2000
VOICE_RESPONSE_LATENCY_TARGET_MS=800

# Cost Limits
VOICE_DEFAULT_MONTHLY_BUDGET_USD=500
VOICE_MAX_CONCURRENT_CALLS=10
```

## Verification Plan

### End-to-End Testing

1. **Setup**:
   - Provision workspace with Twilio phone number
   - Configure VoiceConfiguration (enable, set budget, voice IDs)
   - Start voice_call_worker

2. **Trigger Call**:
   - From chat portal, agent calls `initiate_phone_call` tool
   - Verify CallSession created with QUEUED status

3. **Worker Processing**:
   - Worker claims job, updates to INITIATING
   - Pre-call context gathered from memory/knowledge
   - Twilio call initiated, customer phone rings

4. **Real-time Conversation**:
   - Customer answers
   - Consent message played
   - Customer provides consent (DTMF)
   - Real-time conversation:
     - Customer speaks → Deepgram → transcript
     - Transcript → LLM → response
     - Response → ElevenLabs → audio
   - SSE events stream to frontend

5. **Post-call**:
   - Call ends (customer hangs up or timeout)
   - Transcript stored as ConversationMessages
   - Summary generated via LLM
   - Cost records created
   - CallSession status: COMPLETED

6. **Verification**:
   - Check execution_conversation has transcript messages
   - Verify summary and action_items populated
   - Confirm cost_total_usd calculated correctly
   - Audit events recorded
   - If linked to AgentRun, verify AgentRunMemoryItems created

### Unit Testing

- Database models (constraints, indexes)
- Cost calculation logic
- Phone number validation
- Budget enforcement
- Context bucket priority sorting
- Transcript segment ordering

### Integration Testing

- Twilio webhook flow (mock Twilio API)
- Deepgram WebSocket (mock STT responses)
- ElevenLabs streaming (mock TTS responses)
- SSE event streaming
- Worker job claiming (concurrent safety)

## Key Architectural Decisions

1. **Database-Backed Queue**: Follows existing pattern, no Celery needed
2. **Separate Django App**: Clean isolation for voice-specific concerns
3. **execution_conversation Pattern**: Reuses AgentRun isolation pattern for transcript storage
4. **SSE for Events**: Consistent with existing real-time pattern
5. **WebSocket Only for Twilio**: Required by Twilio Media Streams, isolated to voice layer
6. **Static Context (MVP)**: Defer background retrieval to Phase 2 for simplicity

## Cost Estimates

**Per 5-minute call**:
- Twilio: $0.075
- Deepgram: $0.0215
- ElevenLabs: ~$0.15 (500 chars/min TTS)
- LLM: ~$0.10 (varies by conversation complexity)
- **Total**: ~$0.35-0.50 per call

**At enterprise scale (1000 calls/month)**:
- Monthly cost: ~$350-500
- Recommend workspace budget: $500-1000/month

## Dependencies

**Python Packages** (add to requirements.txt):
```
twilio>=8.0.0
websockets>=12.0
deepgram-sdk>=3.0.0  # or httpx for WebSocket direct
elevenlabs>=1.0.0
```

**External Services**:
- Twilio account with Voice API enabled
- Deepgram API account
- ElevenLabs API account

**Infrastructure**:
- WebSocket support (ASGI server: Uvicorn, Daphne, or Hypercorn)
- Public webhook URL for Twilio callbacks

## Rollout Plan

1. **Phase 0: Setup**
   - Create Django app structure
   - Define models and migrations
   - Add settings configuration

2. **Phase 1: Core Pipeline**
   - Implement worker (job claiming)
   - Twilio integration (call initiation, webhooks)
   - Basic real-time pipeline (STT → LLM → TTS)

3. **Phase 2: Integration**
   - MCP tool implementation
   - Memory integration (transcript storage)
   - Cost tracking

4. **Phase 3: Polish**
   - SSE events
   - Summary generation
   - Audit trail
   - Budget enforcement

5. **Phase 4: Testing**
   - End-to-end testing
   - Load testing (concurrent calls)
   - Cost validation

6. **Phase 5: Production**
   - Deploy worker to production
   - Provision Twilio numbers
   - Enable for beta workspaces
