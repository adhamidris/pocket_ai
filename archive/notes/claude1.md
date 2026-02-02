# AI-Powered Phone Calls - Comprehensive Architecture Document

## Executive Summary

This document provides the complete architectural design for integrating AI-powered phone call capabilities into the PocketAI platform. The system enables orchestrator agents and sub-agents to conduct real-time, human-like phone conversations with customers using:

- **Twilio** for telephony infrastructure
- **Deepgram** for real-time speech-to-text (streaming)
- **ElevenLabs** for premium text-to-speech synthesis
- **Claude/GPT** for conversation intelligence
- **Django + PostgreSQL** for orchestration and persistence

The architecture follows existing platform patterns: database-backed job queues (no Celery), SSE for real-time events, and tight integration with the agent/memory/knowledge systems.

**Target Performance**: <800ms end-to-end latency (customer speaks → AI responds)

**Target Cost**: $0.35-0.65 per 5-minute call at enterprise scale

---

## Business Context

### Use Cases

1. **Customer Outreach**: Sales calls, follow-ups, appointment reminders
2. **Customer Service**: Support calls, issue resolution, information gathering
3. **Collections**: Payment reminders, negotiation, follow-up scheduling
4. **Retention**: Proactive check-ins, feedback collection, renewal discussions
5. **Verification**: Identity verification, transaction confirmation, compliance calls

### Platform Integration

The voice call feature integrates with the existing multi-agent platform:

```
Workspace (BusinessProfile)
  ├── Orchestrator Agents (AgentProfile)
  │     ├── Sub-agents (AgentRun) ────┐
  │     └── Tools ────────────────────┼─── initiate_phone_call()
  │                                    │
  ├── Knowledge Base (RAG)            │
  ├── Memory System                   │
  └── Phone Numbers ─────────────────►│
                                       ▼
                                  CallSession
                                 (voice conversation)
```

### Inter-Department Communication

Example scenario:
```
Fraud Department Agent (reviewing suspicious transaction)
  ↓ detects need for customer verification call
  ↓ calls: initiate_phone_call(
      phone_number=customer.phone,
      objective="Verify transaction TXN-12345",
      context_items=[transaction_details, customer_history]
    )
  ↓
CallSession created, queued for processing
  ↓
Voice worker processes call
  ↓ during call: customer mentions a different card
  ↓ background agent retrieves card details from knowledge base
  ↓ injects into call context
  ↓
Agent confirms transaction, updates fraud case
  ↓
Call summary posted to Fraud agent's memory
```

---

## System Architecture

### High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          PocketAI Platform                               │
│                                                                          │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐           │
│  │ Chat Portal  │────►│ Orchestrator │────►│  Sub-agents  │           │
│  │    (UI)      │     │    Agent     │     │  (AgentRun)  │           │
│  └──────────────┘     └──────┬───────┘     └──────┬───────┘           │
│                              │                     │                    │
│                              │ initiate_phone_call │                    │
│                              ▼                     ▼                    │
│                        ┌──────────────────────────────┐                │
│                        │   CallSession (QUEUED)       │                │
│                        │   - objective                │                │
│                        │   - customer phone           │                │
│                        │   - pre-call context         │                │
│                        └──────────┬───────────────────┘                │
│                                   │                                     │
│  ┌────────────────────────────────▼─────────────────────────────────┐  │
│  │               Voice Call Worker (Polling Loop)                    │  │
│  │  - Claims CallSession (SELECT FOR UPDATE SKIP LOCKED)            │  │
│  │  - Gathers context from Memory + Knowledge                       │  │
│  │  - Initiates Twilio call                                         │  │
│  └────────────────────────────────┬─────────────────────────────────┘  │
│                                   │                                     │
└───────────────────────────────────┼─────────────────────────────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │   Twilio Media Streams        │
                    │   (WebSocket Handler)         │
                    └───────────┬───────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
┌───────────────┐     ┌─────────────────┐     ┌───────────────┐
│   Deepgram    │     │  Claude/GPT-4   │     │  ElevenLabs   │
│  STT Stream   │────►│  Conversation   │────►│  TTS Stream   │
│   (WebSocket) │     │     Agent       │     │  (WebSocket)  │
└───────────────┘     └────────┬────────┘     └───────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Context Bucket     │
                    │  (Dynamic Injection)│
                    └──────────▲──────────┘
                               │
                    ┌──────────┴──────────┐
                    │ Background Retrieval│
                    │      Agent          │
                    │ (Knowledge Search)  │
                    └─────────────────────┘
```

### Data Flow Sequence

#### 1. Call Initiation

```
User: "Call customer X about invoice #123"
  ↓
Orchestrator Agent processes request
  ↓
Calls initiate_phone_call tool:
  {
    phone_number: "+1234567890",
    objective: "Discuss overdue invoice #123",
    customer_id: "cust_xyz",
    language: "en"
  }
  ↓
Tool handler:
  1. Validates phone number (E.164 format)
  2. Checks workspace voice config (enabled? budget?)
  3. Pre-gathers context:
     - Customer profile
     - Invoice details from knowledge base
     - Recent conversation history
     - Relevant SOPs
  4. Creates CallSession:
     - status: QUEUED
     - context_snapshot: {...pre-gathered context...}
     - links to: AgentProfile, AgentRun, Conversation
  ↓
Returns: {call_session_id: "uuid", status: "queued"}
```

#### 2. Worker Processing

```
voice_call_worker (polling loop every 1s)
  ↓
SELECT FOR UPDATE SKIP LOCKED
FROM call_session
WHERE status = 'queued'
ORDER BY queued_at
LIMIT 1
  ↓
Claims job:
  - status → INITIATING
  - lease_expires_at → now + 10 minutes
  - attempt_count += 1
  ↓
Loads context_snapshot
  ↓
Initiates Twilio call:
  POST /v1/Accounts/{sid}/Calls.json
  {
    From: workspace.phone_number,
    To: customer.phone_number,
    Url: "https://platform.com/voice/webhook/{call_session_id}",
    StatusCallback: "https://platform.com/voice/status/{call_session_id}",
    Record: true
  }
  ↓
Stores twilio_call_sid in CallSession
  ↓
Status → RINGING
```

#### 3. Real-Time Conversation (WebSocket Pipeline)

```
Customer's phone rings
  ↓
Customer answers
  ↓
Twilio calls webhook → returns TwiML:
  <Response>
    <Say>This call may be recorded for quality purposes...</Say>
    <Gather numDigits="1" action="/consent">
      <Say>Press 1 to continue, or hang up to decline.</Say>
    </Gather>
  </Response>
  ↓
Customer presses 1
  ↓
Consent recorded in CallSession
  ↓
TwiML starts Media Stream:
  <Connect>
    <Stream url="wss://platform.com/voice/stream/{call_session_id}" />
  </Connect>
  ↓
WebSocket connection established
  ↓
┌─────────────────────────────────────────────────────────┐
│              Real-time Pipeline (asyncio)                │
│                                                          │
│  Customer speaks                                         │
│    ↓ audio chunks (μ-law, 8kHz)                         │
│  Twilio WebSocket                                        │
│    ↓ forward to Deepgram                                │
│  Deepgram STT (streaming)                               │
│    ↓ transcript chunks (interim + final)                │
│  On final transcript:                                    │
│    ├─► Log CallEvent (type: transcript, speaker: customer)
│    ├─► Trigger background retrieval (async, non-blocking)
│    ├─► Load context from CallContextBucket             │
│    ├─► Build LLM prompt:                               │
│    │     - System: agent constitution + objective       │
│    │     - Context: pre-call snapshot + live injections │
│    │     - History: conversation so far                 │
│    │     - Latest: customer utterance                   │
│    └─► Stream LLM response:                            │
│          ├─► Log CallEvent (type: llm_response)        │
│          └─► Send to ElevenLabs TTS (streaming)        │
│                ↓ audio chunks (μ-law)                   │
│              Twilio WebSocket                           │
│                ↓                                         │
│              Customer hears AI response                 │
│                                                          │
└─────────────────────────────────────────────────────────┘

Meanwhile, in parallel:

Background Retrieval Agent (separate worker, 500ms polling)
  ↓
Monitors active CallSessions
  ↓
For each new customer utterance:
  1. Extract potential queries (NER, intent detection)
     - Named entities (product names, order IDs)
     - Question patterns ("what is...", "how do I...")
  2. Search knowledge base (KnowledgeSearchService)
  3. Create CallContextBucket entries:
     - source: live_retrieval
     - priority: based on relevance
     - content: formatted snippet
     - trigger_phrase: original query
     - expires_at: now + 5 minutes
  ↓
Context available for next LLM turn
```

#### 4. Call Termination

```
Customer hangs up (or timeout/max duration)
  ↓
Twilio sends StatusCallback: status=completed
  ↓
Webhook handler:
  1. Updates CallSession:
     - status: COMPLETED
     - ended_at: now
     - duration_seconds: calculate
  2. Triggers post-call processing (async task)
  ↓
Post-call processor:
  1. Finalize transcript:
     - Fetch all CallEvents (type: transcript)
     - Create CallTranscriptSegments (ordered, speaker-labeled)
  2. Store in execution_conversation:
     - Create Conversation if not exists
     - For each segment → ConversationMessage
       - sender: CUSTOMER or AI
       - body: transcript text
       - metadata: timestamps, language
  3. Generate summary (LLM):
     - Prompt: "Summarize this call, extract key points"
     - Output: {summary, action_items, outcome}
     - Store in CallSession
  4. Extract memory items (if linked to AgentRun):
     - Facts: customer preferences, issues mentioned
     - Decisions: commitments made, agreements
     - Follow-ups: required actions
     - Create AgentRunMemoryItems
  5. Calculate costs:
     - Twilio: duration_minutes * $0.015
     - Deepgram: duration_minutes * $0.0043
     - ElevenLabs: tts_characters * $0.0003
     - LLM: tokens * rate
     - Create VoiceCostRecords
     - Update CallSession.cost_total_usd
  6. Create audit events
  7. Queue conversation compaction job
```

---

## Database Schema (Detailed)

### 1. VoicePhoneNumber

**Purpose**: Per-workspace Twilio phone numbers

```python
class VoicePhoneNumber(models.Model):
    id = UUIDField(primary_key=True)
    business_profile = ForeignKey(BusinessProfile)

    # Twilio details
    twilio_sid = CharField(max_length=64, unique=True, db_index=True)
    phone_number = CharField(max_length=20, db_index=True)  # E.164: +12125551234
    friendly_name = CharField(max_length=128)

    # Status
    status = CharField(choices=[
        ("provisioning", "Provisioning"),
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("released", "Released"),
    ], default="provisioning", db_index=True)

    # Configuration
    capabilities = JSONField(default=dict)  # {voice: true, sms: false}
    monthly_cost_usd = DecimalField(max_digits=8, decimal_places=4)

    metadata = JSONField(default=dict)
    created_at = DateTimeField(auto_now_add=True, db_index=True)
    updated_at = DateTimeField(auto_now=True)
```

**Indexes**:
- `(business_profile, status)`: Quickly find active numbers for workspace
- `twilio_sid`: Webhook lookups

### 2. CallSession

**Purpose**: Single phone call instance (the core entity)

```python
class CallSession(models.Model):
    id = UUIDField(primary_key=True)
    business_profile = ForeignKey(BusinessProfile)
    agent_profile = ForeignKey(AgentProfile)
    phone_number = ForeignKey(VoicePhoneNumber, on_delete=PROTECT)

    # Context links (optional)
    agent_run = ForeignKey(AgentRun, null=True)  # If triggered by sub-agent
    conversation = ForeignKey(Conversation, null=True)  # Anchor conversation
    execution_conversation = ForeignKey(Conversation, null=True)  # Transcript storage
    customer = ForeignKey(Customer, null=True)

    # Call details
    to_phone_number = CharField(max_length=20)  # E.164
    twilio_call_sid = CharField(max_length=64, db_index=True)
    source = CharField(choices=[
        ("agent_run", "Agent Run"),
        ("chat", "Chat"),
        ("automation", "Automation"),
        ("api", "API"),
    ])
    status = CharField(choices=[
        ("queued", "Queued"),
        ("initiating", "Initiating"),
        ("ringing", "Ringing"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("no_answer", "No Answer"),
        ("busy", "Busy"),
        ("cancelled", "Cancelled"),
    ], default="queued", db_index=True)

    # Pre-call context (frozen snapshot)
    objective = TextField()  # "Discuss overdue invoice #123"
    context_snapshot = JSONField(default=dict)  # Pre-gathered context
    customer_snapshot = JSONField(default=dict)  # Customer details

    # Call configuration
    language = CharField(max_length=8, default="en")  # en, ar
    voice_id = CharField(max_length=64)  # ElevenLabs voice ID
    consent_obtained = BooleanField(default=False)
    consent_obtained_at = DateTimeField(null=True)

    # Timing
    queued_at = DateTimeField(auto_now_add=True)
    initiated_at = DateTimeField(null=True)
    answered_at = DateTimeField(null=True)
    ended_at = DateTimeField(null=True)
    duration_seconds = PositiveIntegerField(default=0)

    # Post-call results
    summary = TextField()  # LLM-generated summary
    action_items = JSONField(default=list)  # [{description, priority, due_date}]
    outcome = CharField(max_length=64)  # success, partial, failed

    # Cost tracking (detailed breakdown)
    cost_twilio_usd = DecimalField(max_digits=10, decimal_places=6, default=0)
    cost_deepgram_usd = DecimalField(max_digits=10, decimal_places=6, default=0)
    cost_elevenlabs_usd = DecimalField(max_digits=10, decimal_places=6, default=0)
    cost_llm_usd = DecimalField(max_digits=10, decimal_places=6, default=0)
    cost_total_usd = DecimalField(max_digits=10, decimal_places=6, default=0)

    # Error handling
    error_code = CharField(max_length=64)
    error_detail = TextField()

    # Worker coordination (for database-backed queue)
    lease_expires_at = DateTimeField(null=True, db_index=True)
    attempt_count = PositiveIntegerField(default=0)
    max_attempts = PositiveIntegerField(default=3)

    metadata = JSONField(default=dict)
    created_at = DateTimeField(auto_now_add=True, db_index=True)
    updated_at = DateTimeField(auto_now=True)
```

**Indexes**:
- `(business_profile, status)`: List calls by workspace
- `(agent_profile, status)`: List calls by agent
- `(status, lease_expires_at)`: Worker job claiming
- `twilio_call_sid`: Webhook lookups

### 3. CallEvent

**Purpose**: Append-only event log for call lifecycle

```python
class CallEvent(models.Model):
    id = UUIDField(primary_key=True)
    call_session = ForeignKey(CallSession, on_delete=CASCADE)
    sequence_index = PositiveIntegerField()  # Deterministic ordering

    event_type = CharField(max_length=32, choices=[
        ("initiated", "Call Initiated"),
        ("ringing", "Ringing"),
        ("answered", "Call Answered"),
        ("consent_requested", "Consent Requested"),
        ("consent_granted", "Consent Granted"),
        ("consent_denied", "Consent Denied"),
        ("speech_started", "Speech Started"),
        ("speech_ended", "Speech Ended"),
        ("transcript", "Transcript"),
        ("llm_response", "LLM Response"),
        ("tts_started", "TTS Started"),
        ("tts_completed", "TTS Completed"),
        ("context_injected", "Context Injected"),
        ("dtmf_received", "DTMF Received"),
        ("hold_started", "Hold Started"),
        ("hold_ended", "Hold Ended"),
        ("transfer_initiated", "Transfer Initiated"),
        ("ended", "Call Ended"),
        ("error", "Error"),
    ])

    speaker = CharField(max_length=16, choices=[
        ("customer", "Customer"),
        ("agent", "Agent"),
        ("system", "System"),
    ])

    content = TextField()  # Transcript text, error message, etc.
    duration_ms = PositiveIntegerField(default=0)
    confidence = FloatField(null=True)  # STT confidence score
    latency_ms = PositiveIntegerField(null=True)  # Pipeline latency

    payload = JSONField(default=dict)  # Event-specific metadata
    created_at = DateTimeField(auto_now_add=True, db_index=True)
```

**Indexes**:
- `(call_session, sequence_index)`: Ordered event retrieval
- **UNIQUE constraint**: `(call_session, sequence_index)`

### 4. CallTranscriptSegment

**Purpose**: Finalized transcript with speaker diarization

```python
class CallTranscriptSegment(models.Model):
    id = UUIDField(primary_key=True)
    call_session = ForeignKey(CallSession, on_delete=CASCADE)
    segment_index = PositiveIntegerField()

    speaker = CharField(max_length=16, choices=[
        ("customer", "Customer"),
        ("agent", "Agent"),
    ])

    content = TextField()  # Finalized transcript text
    start_time_ms = PositiveIntegerField()  # From call start
    end_time_ms = PositiveIntegerField()
    confidence = FloatField(default=1.0)
    language = CharField(max_length=8, default="en")

    metadata = JSONField(default=dict)
    created_at = DateTimeField(auto_now_add=True)
```

**Indexes**:
- `(call_session, segment_index)`: Ordered transcript playback

### 5. CallContextBucket

**Purpose**: Dynamic context injection during call

```python
class CallContextBucket(models.Model):
    id = UUIDField(primary_key=True)
    call_session = ForeignKey(CallSession, on_delete=CASCADE)

    source = CharField(max_length=32, choices=[
        ("pre_call", "Pre-call"),
        ("live_retrieval", "Live Retrieval"),
        ("memory", "Memory"),
        ("knowledge", "Knowledge"),
    ])

    priority = PositiveSmallIntegerField(default=50)  # 0=highest, 100=lowest
    content = TextField()  # Formatted context snippet
    token_count = PositiveIntegerField()
    relevance_score = FloatField(default=1.0)
    trigger_phrase = CharField(max_length=256)  # What triggered this retrieval

    is_consumed = BooleanField(default=False)
    consumed_at = DateTimeField(null=True)
    expires_at = DateTimeField(null=True)

    metadata = JSONField(default=dict)
    created_at = DateTimeField(auto_now_add=True, db_index=True)
```

**Indexes**:
- `(call_session, is_consumed)`: Fetch unconsumed context
- **Ordering**: `(-priority, created_at)` - higher priority first

### 6. VoiceCostRecord

**Purpose**: Granular cost tracking for billing

```python
class VoiceCostRecord(models.Model):
    id = UUIDField(primary_key=True)
    business_profile = ForeignKey(BusinessProfile)
    call_session = ForeignKey(CallSession)

    service = CharField(max_length=24, choices=[
        ("twilio", "Twilio"),
        ("deepgram", "Deepgram"),
        ("elevenlabs", "ElevenLabs"),
        ("llm", "LLM"),
    ])

    quantity = DecimalField(max_digits=12, decimal_places=4)
    unit = CharField(max_length=32)  # minutes, characters, tokens
    unit_cost_usd = DecimalField(max_digits=10, decimal_places=6)
    total_cost_usd = DecimalField(max_digits=10, decimal_places=6)

    metadata = JSONField(default=dict)
    created_at = DateTimeField(auto_now_add=True, db_index=True)
```

**Indexes**:
- `(business_profile, created_at)`: Monthly cost rollups

### 7. VoiceCallAuditEvent

**Purpose**: Compliance audit trail

```python
class VoiceCallAuditEvent(models.Model):
    id = UUIDField(primary_key=True)
    business_profile = ForeignKey(BusinessProfile)
    call_session = ForeignKey(CallSession)
    actor_agent = ForeignKey(AgentProfile, null=True)

    action = CharField(max_length=32, choices=[
        ("initiated", "Call Initiated"),
        ("answered", "Call Answered"),
        ("consent_recorded", "Consent Recorded"),
        ("transcript_stored", "Transcript Stored"),
        ("summary_generated", "Summary Generated"),
        ("ended", "Call Ended"),
        ("failed", "Call Failed"),
    ])

    description = TextField()
    metadata = JSONField(default=dict)

    occurred_at = DateTimeField(default=timezone.now, db_index=True)
    created_at = DateTimeField(auto_now_add=True)
```

**Indexes**:
- `(business_profile, occurred_at)`: Audit log queries
- `(call_session, occurred_at)`: Per-call audit trail

### 8. VoiceConfiguration

**Purpose**: Per-workspace voice settings

```python
class VoiceConfiguration(models.Model):
    id = UUIDField(primary_key=True)
    business_profile = OneToOneField(BusinessProfile, related_name="voice_config")

    enabled = BooleanField(default=False)
    monthly_budget_usd = DecimalField(max_digits=10, decimal_places=2, null=True)
    concurrent_call_limit = PositiveSmallIntegerField(default=5)

    # Default voice settings
    default_language = CharField(max_length=8, default="en")
    default_voice_id_en = CharField(max_length=64)  # ElevenLabs voice ID
    default_voice_id_ar = CharField(max_length=64)

    # Consent templates
    consent_message_en = TextField(
        default="This call may be recorded for quality purposes. Do you consent to continue?"
    )
    consent_message_ar = TextField(
        default="قد يتم تسجيل هذه المكالمة لأغراض الجودة. هل توافق على المتابعة؟"
    )

    # Alert thresholds
    budget_alert_threshold = DecimalField(max_digits=5, decimal_places=2, default=0.8)

    metadata = JSONField(default=dict)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
```

---

## Real-Time Pipeline (Technical Deep Dive)

### Latency Budget (Target: <800ms)

| Component | Target Latency | Notes |
|-----------|---------------|-------|
| Twilio audio transmission | ~50ms | Network RTT |
| Deepgram STT | 100-200ms | Streaming with interim results |
| Background retrieval | 200-400ms | Parallel, non-blocking |
| LLM first token | 200-400ms | Streaming mode, prompt caching |
| ElevenLabs TTS first audio | 100-300ms | Streaming synthesis |
| Twilio audio playback | ~50ms | Network RTT |
| **Total end-to-end** | **600-1200ms** | Can be optimized further |

### Optimization Strategies

1. **Streaming everywhere**: STT, LLM, TTS all use streaming APIs
2. **Parallel processing**: Background retrieval doesn't block LLM response
3. **Prompt caching**: Cache system prompts + agent constitution
4. **Context pre-loading**: Load likely context into bucket preemptively
5. **Connection pooling**: Keep WebSocket connections warm
6. **Edge deployment**: Minimize geographic latency

### WebSocket Handler (Pseudocode)

```python
class TwilioMediaStreamHandler:
    def __init__(self, call_session_id):
        self.call_session = CallSession.objects.get(id=call_session_id)
        self.deepgram_ws = DeepgramStreamingClient(
            language=self.call_session.language,
            on_transcript=self.handle_transcript,
            on_utterance_end=self.handle_utterance_end,
        )
        self.elevenlabs_ws = ElevenLabsStreamingClient(
            voice_id=self.call_session.voice_id,
            on_audio=self.send_to_twilio,
        )
        self.context_manager = CallContextManager(self.call_session)
        self.conversation_history = []
        self.is_speaking = False

    async def handle_connection(self, websocket):
        # Connect streaming clients
        await self.deepgram_ws.connect()
        await self.elevenlabs_ws.connect()

        # Main loop: process Twilio messages
        async for message in websocket:
            data = json.loads(message)

            if data["event"] == "media":
                # Inbound audio from customer
                audio_payload = base64.b64decode(data["media"]["payload"])
                await self.deepgram_ws.send_audio(audio_payload)

            elif data["event"] == "start":
                self.log_event("answered")

            elif data["event"] == "stop":
                await self.finalize_call()

    async def handle_transcript(self, text: str, is_final: bool):
        if not is_final:
            return  # Ignore interim results

        if not text.strip():
            return

        # Log transcript event
        self.log_event("transcript", content=text, speaker="customer")
        self.conversation_history.append({"role": "user", "content": text})

        # Trigger background retrieval (fire and forget)
        asyncio.create_task(self.trigger_retrieval(text))

        # Generate LLM response
        response = await self.generate_response(text)

        # Log LLM response
        self.log_event("llm_response", content=response, speaker="agent")
        self.conversation_history.append({"role": "assistant", "content": response})

        # Synthesize speech
        await self.elevenlabs_ws.synthesize(response)

    async def generate_response(self, customer_input: str) -> str:
        # Load context from bucket
        context_items = self.context_manager.get_relevant_context(
            trigger=customer_input,
            max_tokens=2000
        )

        # Build prompt
        system_prompt = self.build_system_prompt()
        context_prompt = "\n".join([item.content for item in context_items])

        messages = [
            {"role": "system", "content": system_prompt + "\n\nContext:\n" + context_prompt},
            *self.conversation_history[-10:],  # Last 10 turns
        ]

        # Stream LLM response
        response_text = ""
        async for chunk in stream_llm(messages):
            response_text += chunk

        return response_text

    async def trigger_retrieval(self, customer_utterance: str):
        # Extract queries (lightweight NER)
        queries = extract_retrieval_queries(customer_utterance)

        # Search knowledge base
        for query in queries[:3]:
            results = await search_knowledge(
                query,
                business_profile=self.call_session.business_profile,
                agent_profile=self.call_session.agent_profile,
            )

            # Inject into context bucket
            for snippet in results.snippets[:3]:
                CallContextBucket.objects.create(
                    call_session=self.call_session,
                    source="live_retrieval",
                    priority=calculate_priority(snippet),
                    content=format_snippet(snippet),
                    token_count=estimate_tokens(snippet.content),
                    relevance_score=snippet.confidence_score,
                    trigger_phrase=query,
                    expires_at=timezone.now() + timedelta(minutes=5),
                )

    def send_to_twilio(self, audio_bytes: bytes):
        # Send audio to Twilio WebSocket
        payload = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {
                "payload": base64.b64encode(audio_bytes).decode("utf-8")
            }
        }
        await self.twilio_ws.send(json.dumps(payload))
```

---

## Cost Management

### Cost Breakdown (5-minute call example)

| Service | Unit | Quantity | Rate | Total |
|---------|------|----------|------|-------|
| Twilio Voice | minutes | 5 | $0.015 | $0.075 |
| Deepgram STT | minutes | 5 | $0.0043 | $0.0215 |
| ElevenLabs TTS | characters | 2500 | $0.0003 | $0.75 |
| Claude Sonnet | tokens | 10,000 | varies | $0.10 |
| **Total** | | | | **~$0.95** |

### Budget Enforcement

```python
def check_budget_before_call(business_profile) -> dict:
    config = business_profile.voice_config

    if not config.monthly_budget_usd:
        return {"allowed": True}

    # Get current month spend
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0)
    total_spent = VoiceCostRecord.objects.filter(
        business_profile=business_profile,
        created_at__gte=month_start,
    ).aggregate(Sum("total_cost_usd"))["total"] or Decimal("0")

    remaining = config.monthly_budget_usd - total_spent

    if remaining <= 0:
        return {
            "allowed": False,
            "reason": "monthly_budget_exceeded",
            "spent": total_spent,
            "budget": config.monthly_budget_usd,
        }

    # Alert if threshold reached
    if total_spent >= (config.monthly_budget_usd * config.budget_alert_threshold):
        # Send alert to workspace owner
        send_budget_alert(business_profile, total_spent, config.monthly_budget_usd)

    return {
        "allowed": True,
        "remaining": remaining,
        "spent": total_spent,
    }
```

---

## Security & Compliance

### 1. Call Recording Consent

**Regulatory Requirements**:
- **One-party consent states**: Only one party needs to consent (most US states)
- **Two-party consent states**: Both parties must consent (CA, FL, PA, etc.)
- **International**: GDPR (EU), varies by country

**Implementation**:
```python
def play_consent_message(call_session):
    language = call_session.language
    config = call_session.business_profile.voice_config

    if language == "en":
        message = config.consent_message_en
    elif language == "ar":
        message = config.consent_message_ar
    else:
        message = config.consent_message_en

    # Play via TwiML
    response = VoiceResponse()
    response.say(message, voice="alice", language=language)

    # Gather consent (DTMF)
    gather = Gather(
        num_digits=1,
        action=reverse("voice:consent_response", args=[call_session.id]),
        timeout=10,
    )
    gather.say("Press 1 to continue, or hang up to decline.")
    response.append(gather)

    return str(response)

def record_consent(call_session, granted: bool):
    call_session.consent_obtained = granted
    call_session.consent_obtained_at = timezone.now() if granted else None
    call_session.save()

    # Audit event
    VoiceCallAuditEvent.objects.create(
        business_profile=call_session.business_profile,
        call_session=call_session,
        actor_agent=call_session.agent_profile,
        action="consent_recorded",
        description=f"Recording consent {'granted' if granted else 'denied'}.",
        metadata={"granted": granted, "method": "dtmf"},
    )

    if not granted:
        # Terminate call
        call_session.status = "cancelled"
        call_session.save()
        # Hang up via Twilio API
```

### 2. Data Retention

Follows existing `TenantMemoryConfiguration`:

```python
def apply_retention_policy(call_session):
    memory_config = call_session.business_profile.memory_config

    if not memory_config:
        return

    max_retention_days = memory_config.maximum_retention_days

    if memory_config.purge_enabled and not memory_config.legal_hold and max_retention_days:
        purge_after = timezone.now() + timedelta(days=max_retention_days)

        call_session.metadata["purge_after"] = purge_after.isoformat()
        call_session.save()

        # Schedule deletion job
        # (handled by existing retention_purge.py service)
```

### 3. PII Protection

```python
def redact_sensitive_data(transcript: str) -> str:
    """Redact PII from transcripts before storage."""

    # Credit card numbers
    transcript = re.sub(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "[CARD]", transcript)

    # SSN
    transcript = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]", transcript)

    # Email addresses (optional, context-dependent)
    # transcript = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]", transcript)

    return transcript
```

---

## Monitoring & Observability

### Key Metrics

1. **Call Success Rate**: % of calls that complete successfully
2. **Average Call Duration**: Time from answer to end
3. **Latency P50/P95/P99**: Response latency distribution
4. **Cost per Call**: Average total cost
5. **Budget Utilization**: % of monthly budget consumed
6. **Consent Grant Rate**: % of customers who consent to recording
7. **Transcript Confidence**: Average STT confidence scores

### Logging

```python
# In CallEvent model
def log_call_event(
    call_session,
    event_type,
    speaker="system",
    content="",
    latency_ms=None,
    **kwargs
):
    sequence = call_session.events.count()

    CallEvent.objects.create(
        call_session=call_session,
        sequence_index=sequence,
        event_type=event_type,
        speaker=speaker,
        content=content,
        latency_ms=latency_ms,
        payload=kwargs,
    )
```

### Alerts

- Budget threshold exceeded (80%, 90%, 100%)
- Call failure rate > 10%
- Average latency > 1500ms
- STT confidence < 0.7
- Concurrent calls approaching limit

---

## Phase 1 MVP Scope

### In Scope

✅ Outbound calls only
✅ English language only
✅ Basic call initiation via `initiate_phone_call` tool
✅ Real-time STT/TTS pipeline (Deepgram + ElevenLabs)
✅ Consent message and recording
✅ Transcript storage as ConversationMessages
✅ Post-call summary generation (LLM)
✅ Cost tracking and budget enforcement
✅ SSE event streaming for real-time UI updates
✅ Basic audit trail
✅ Pre-call context gathering (static snapshot from memory/knowledge)

### Out of Scope (Deferred to Phase 2+)

❌ Arabic language support
❌ Background retrieval agent (live context injection during call)
❌ Inbound call handling
❌ Call transfer to human
❌ Advanced voice cloning (custom voice training)
❌ Call recording storage (S3/media files)
❌ SMS integration
❌ Multi-party conference calls
❌ DTMF menu navigation
❌ Advanced analytics dashboard

---

## Rollout Strategy

### Phase 0: Infrastructure Setup (Week 1)
- [ ] Create `apps/voice/` Django app
- [ ] Define database models
- [ ] Run migrations
- [ ] Add settings to `settings.py`
- [ ] Set up Twilio account and provision test number
- [ ] Set up Deepgram API account
- [ ] Set up ElevenLabs API account

### Phase 1: Core Pipeline (Weeks 2-3)
- [ ] Implement `voice_call_worker.py` (job claiming)
- [ ] Implement Twilio call initiation
- [ ] Implement Twilio webhooks (TwiML generation)
- [ ] Implement WebSocket handler for Media Streams
- [ ] Implement Deepgram streaming client
- [ ] Implement ElevenLabs streaming client
- [ ] End-to-end test: successful outbound call

### Phase 2: Integration (Week 4)
- [ ] Add `initiate_phone_call` MCP tool
- [ ] Implement pre-call context gathering
- [ ] Implement post-call transcript storage
- [ ] Implement post-call summary generation
- [ ] Test: Agent initiates call from chat portal

### Phase 3: Cost & Compliance (Week 5)
- [ ] Implement cost tracking service
- [ ] Implement budget enforcement
- [ ] Implement consent handling
- [ ] Implement audit trail
- [ ] Test: Cost calculations, budget alerts

### Phase 4: Real-time Events (Week 6)
- [ ] Implement SSE endpoints for call events
- [ ] Frontend integration (display live call status)
- [ ] Test: Real-time event streaming

### Phase 5: Testing & Polish (Week 7)
- [ ] End-to-end testing
- [ ] Load testing (concurrent calls)
- [ ] Error handling and retries
- [ ] Documentation

### Phase 6: Production Rollout (Week 8)
- [ ] Deploy to staging environment
- [ ] Beta test with select workspaces
- [ ] Monitor metrics and costs
- [ ] Gradual rollout to all workspaces

---

## Dependencies & Prerequisites

### Python Packages

```
twilio>=8.0.0
websockets>=12.0
deepgram-sdk>=3.0.0
elevenlabs>=1.0.0
```

### External Services

1. **Twilio**:
   - Account SID and Auth Token
   - Voice-enabled phone number(s)
   - Webhook URLs configured

2. **Deepgram**:
   - API key
   - Streaming STT enabled

3. **ElevenLabs**:
   - API key
   - Voice IDs for English (and Arabic in Phase 2)

### Infrastructure

- **ASGI server**: Uvicorn, Daphne, or Hypercorn (for WebSocket support)
- **Public webhook URL**: For Twilio callbacks (use ngrok for local dev)
- **Worker process**: Long-running `voice_call_worker` (similar to existing workers)

---

## Risk Mitigation

### Risk 1: Latency exceeds target (>1s)

**Mitigation**:
- Use streaming everywhere (STT, LLM, TTS)
- Enable prompt caching for system prompts
- Consider edge deployment (Cloudflare Workers, AWS Lambda@Edge)
- Monitor P95/P99 latency metrics

### Risk 2: Costs spiral out of control

**Mitigation**:
- Hard budget limits per workspace
- Real-time cost tracking
- Alert thresholds (80%, 90%, 100%)
- Default conservative budget ($500/month)

### Risk 3: Poor call quality (low STT accuracy, robotic TTS)

**Mitigation**:
- Use premium models (Deepgram Nova-2, ElevenLabs)
- Confidence score filtering (reject low-confidence transcripts)
- Voice cloning for brand consistency
- Continuous monitoring of call success metrics

### Risk 4: Regulatory compliance issues

**Mitigation**:
- Mandatory consent message
- Audit trail for all calls
- Retention policies (follow existing memory policies)
- Legal review before launch

### Risk 5: Twilio service outages

**Mitigation**:
- Implement exponential backoff retries
- Graceful error handling
- Fallback: queue call for later
- Monitor Twilio status page

---

## Success Criteria

1. **Latency**: 90% of calls have <1s response latency
2. **Success Rate**: 95% of initiated calls complete successfully
3. **Cost**: Average cost per call < $0.75
4. **Accuracy**: STT confidence > 0.8 for 90% of transcripts
5. **Adoption**: 20% of workspaces use voice calling within 3 months
6. **Budget**: <5% of workspaces exceed monthly budget
7. **Consent**: >90% consent rate

---

## Future Enhancements (Roadmap)

### Phase 2 (Q2)
- Arabic language support
- Background retrieval agent (live context injection)
- Advanced prompt engineering (persona, tone control)

### Phase 3 (Q3)
- Inbound call handling
- Call transfer to human agents
- SMS fallback (if call fails)

### Phase 4 (Q4)
- Custom voice cloning
- Multi-party conference calls
- Call recording storage and playback
- Advanced analytics dashboard

---

## Conclusion

This architecture provides a robust, scalable foundation for AI-powered phone calling that:

1. **Integrates seamlessly** with existing agent/memory/knowledge systems
2. **Follows established patterns** (database-backed queues, SSE, execution_conversation)
3. **Maintains low latency** (<800ms target) for natural conversations
4. **Enforces cost controls** (budget limits, tracking)
5. **Ensures compliance** (consent, audit trail, retention)
6. **Scales horizontally** (worker processes, concurrent calls)

The phased rollout strategy minimizes risk while delivering value incrementally, starting with a focused MVP that can be enhanced based on real-world usage and feedback.
