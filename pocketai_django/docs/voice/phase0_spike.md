# Voice Calls — Phase 0 Spike (Twilio ↔ Deepgram ↔ LLM ↔ ElevenLabs)

**Status:** Implemented; dev‑only (not production).

This doc explains how to run the **Phase 0** voice spike locally. It validates:

- Twilio outbound call initiation + webhook flow
- Mandatory AI disclosure + **mandatory recording consent** (DTMF) before recording starts
- Twilio Media Streams bidirectional audio (server sends audio back)
- Deepgram streaming STT (mulaw/8k)
- DeepSeek/OpenAI response generation
- ElevenLabs streaming TTS (telephony format `ulaw_8000`)

This is not production guidance; it’s a feasibility spike.

---

## 1) Install deps

From `pocketai_django/`:

```bash
pip install -r requirements.txt
```

---

## 2) Required environment variables

### Twilio (required)

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER` (E.164, e.g. `+15551234567`)
- `TWILIO_WEBHOOK_BASE_URL` (public HTTPS base url that points to your Django server; no trailing slash)
  - Example: `https://<your-webhook-ngrok-domain>`

### WebSocket base URL (required)

- `VOICE_SPIKE_WS_BASE_URL` (public WSS base url that points to the spike WS server; no trailing slash)
  - Example: `wss://<your-ws-ngrok-domain>`

### Deepgram STT (required for real transcription)

- `DEEPGRAM_API_KEY`
- `DEEPGRAM_MODEL` (optional, default: `nova-2`)

### ElevenLabs TTS (required for real speech)

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID` (or set `ELEVENLABS_DEFAULT_VOICE_EN`)
- `ELEVENLABS_MODEL_ID` (optional, default: `eleven_flash_v2_5`)
- `ELEVENLABS_OUTPUT_FORMAT` (optional, default: `ulaw_8000`)

### LLM (DeepSeek/OpenAI)

Pick one:

- **OpenAI**
  - `LLM_PROVIDER=openai`
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL` (optional)

- **DeepSeek**
  - `LLM_PROVIDER=deepseek`
  - `DEEPSEEK_API_KEY`
  - `DEEPSEEK_MODEL` (optional)

---

## 3) Run migrations

```bash
RAG_USE_MCP_ORCHESTRATOR=false python manage.py migrate
```

(`RAG_USE_MCP_ORCHESTRATOR=false` avoids slow warmups when you’re only testing voice.)

---

## 4) Start servers (2 processes)

### A) Start Django (webhooks)

```bash
RAG_USE_MCP_ORCHESTRATOR=false python manage.py runserver 0.0.0.0:8000
```

### B) Start the Media Streams WebSocket server

```bash
RAG_USE_MCP_ORCHESTRATOR=false python manage.py voice_spike_ws_server --host 0.0.0.0 --port 8081
```

---

## 5) Expose both ports publicly (example: ngrok)

You need *two* public endpoints:
- HTTPS → Django :8000
- WSS → WS server :8081

One approach is an ngrok config with two tunnels (example):

```yaml
version: "2"
tunnels:
  webhook:
    addr: 8000
    proto: http
  ws:
    addr: 8081
    proto: http
```

Then:
- Set `TWILIO_WEBHOOK_BASE_URL` to the `webhook` tunnel HTTPS URL.
- Set `VOICE_SPIKE_WS_BASE_URL` to the `ws` tunnel HTTPS URL but with `wss://`.

---

## 6) Start a call (agent-initiated single call)

Send a POST to:

`POST /voice/spike/start/`

Example:

```bash
curl -sS -X POST "${TWILIO_WEBHOOK_BASE_URL}/voice/spike/start/" \
  -H "content-type: application/json" \
  -d '{
    "to_phone_number": "+201234567890",
    "objective": "Quick intro and confirm you can hear me.",
    "call_type": "service",
    "language": "en"
  }'
```

You should see:
- the callee receives a call
- disclosure + recording consent request
- after pressing `1`, the assistant speaks back using the pipeline

---

## Troubleshooting

- If Twilio can’t reach your endpoints, verify `TWILIO_WEBHOOK_BASE_URL` is HTTPS and publicly accessible.
- If Twilio connects but you hear silence, verify:
  - `VOICE_SPIKE_WS_BASE_URL` is WSS and points to the WS server
  - ElevenLabs config is correct and `ELEVENLABS_OUTPUT_FORMAT=ulaw_8000`
- If STT never produces text, verify Deepgram config:
  - `DEEPGRAM_API_KEY` present
  - model supports your language
