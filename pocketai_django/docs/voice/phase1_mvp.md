# Voice Calls — Phase 1 MVP Runbook

**Status:** Implemented; dev‑only (not production).

Phase 1 turns the Phase 0 spike into a production-shaped subsystem:

- `initiate_phone_call` MCP tool → creates `CallSession(status=queued)`
- `voice_call_worker` → claims queued sessions and creates Twilio calls
- Twilio webhooks (TwiML/consent/status/recording) under `/voice/twilio/...`
- `voice_ws_server` → Twilio Media Streams WS runtime (STT→LLM→TTS)
- `voice_post_call_worker` → transcript finalization + summary + optional R2 upload

Arabic + dialect QA (Phase 2): `docs/voice/phase2_arabic_qa.md`
Compliance engine (Phase 3): `docs/voice/phase3_policy_engine.md`

## Environment variables

### Voice (owner)
- `VOICE_GLOBAL_ENABLED=true`
- `VOICE_AUTO_CREATE_CONFIG=true` (optional for early dev; creates `VoiceConfiguration` automatically)
- `VOICE_WS_BASE_URL=wss://<public-wss-domain>` (no trailing slash)
- `VOICE_STT_DUAL_STREAM_AR_EN=true` (optional; default `true` for Arabic sessions to improve Arabic↔English code-switch)

### Twilio
- `TWILIO_ACCOUNT_SID=...`
- `TWILIO_AUTH_TOKEN=...`
- `TWILIO_FROM_NUMBER=+15551234567` (fallback if workspace has no `VoicePhoneNumber`)
- `TWILIO_WEBHOOK_BASE_URL=https://<public-https-domain>` (no trailing slash)
- `TWILIO_VALIDATE_SIGNATURES=true` (recommended; set `false` only for debugging URL mismatch)

### Deepgram (STT)
- `DEEPGRAM_API_KEY=...`
- `DEEPGRAM_MODEL=nova-2` (optional)
- `DEEPGRAM_ENDPOINTING_MS=300` (optional)

### ElevenLabs (TTS)
- `ELEVENLABS_API_KEY=...`
- `ELEVENLABS_VOICE_ID=...` (optional; forces a single voice for all languages)
- `ELEVENLABS_DEFAULT_VOICE_EN=...` (required if `ELEVENLABS_VOICE_ID` is not set)
- `ELEVENLABS_DEFAULT_VOICE_AR=...` (recommended for Arabic calls)
- `ELEVENLABS_MODEL_ID=...` or `ELEVENLABS_MODEL_ID_EN=...` / `ELEVENLABS_MODEL_ID_AR=...` (optional)

### LLM
- `LLM_PROVIDER=openai` or `LLM_PROVIDER=deepseek`
- `OPENAI_API_KEY=...` and/or `DEEPSEEK_API_KEY=...`

### Cloudflare R2 (optional recordings)
- `VOICE_R2_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com`
- `VOICE_R2_BUCKET=<bucket>`
- `VOICE_R2_ACCESS_KEY_ID=...`
- `VOICE_R2_SECRET_ACCESS_KEY=...`
- `VOICE_R2_REGION=auto`

## Local dev (ngrok)

You typically need 2 public tunnels:

1) HTTPS tunnel to Django (`:8000`) for Twilio webhooks
2) WSS tunnel to WS server (`:8081`) for Twilio Media Streams

Set:
- `TWILIO_WEBHOOK_BASE_URL` to the HTTPS tunnel base URL
- `VOICE_WS_BASE_URL` to the WSS tunnel base URL

## Run processes

In separate terminals:

1) Django server (webhooks + API):
   - `RAG_WARM_EMBEDDINGS_ON_STARTUP=false ./venv/bin/python manage.py runserver 0.0.0.0:8000`

2) Media Streams WebSocket server:
   - `./venv/bin/python manage.py voice_ws_server --host 0.0.0.0 --port 8081`

3) Call initiation worker:
   - `./venv/bin/python manage.py voice_call_worker --watch`

4) Post-call worker (summary/transcript/recording ingest):
   - `./venv/bin/python manage.py voice_post_call_worker --watch`

## Trigger a call

Phase 1 is “agent initiated”. The intended way to start a call is via the agent/orchestrator using the MCP tool:

- Tool name: `initiate_phone_call`
- Args: `{ "phone_number": "+201234567890", "objective": "Confirm appointment" }`

For debugging you can also call the tool from `manage.py shell`:

```python
from apps.conversations.models import Conversation
from apps.mcp.tools import execute_tool
from apps.mcp.types import ToolExecutionContext

conv = Conversation.objects.order_by("-created_at").first()
execute_tool(
  "initiate_phone_call",
  {"phone_number": "+201234567890", "objective": "Confirm appointment"},
  conversation=conv,
  context=ToolExecutionContext(),
)
```

## Monitoring

- List calls: `GET /api/voice/calls/?business_id=<uuid>`
- Call detail: `GET /api/voice/calls/<call_id>/`
- Live events (SSE): `GET /api/voice/calls/<call_id>/events/`
- Hang up: `POST /api/voice/calls/<call_id>/hangup/`

## Common issues

- 403 on Twilio webhooks: your `TWILIO_WEBHOOK_BASE_URL` does not exactly match what Twilio is calling (signature validation is strict).
- No audio / WS rejected: `VOICE_WS_BASE_URL` must be public and the WS server must be reachable; the `<Stream>` URL includes an auth token.
