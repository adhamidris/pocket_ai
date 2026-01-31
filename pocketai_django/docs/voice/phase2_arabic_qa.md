# Phase 2 — Arabic + Dialect QA (Deepgram + ElevenLabs)

Phase 2 goals:

- Support **Arabic + English** calls in production.
- Validate **Egyptian** + **GCC** accents (STT) and code-switching (Arabic ↔ English).

This phase is intentionally “QA-first”: it adds sane defaults + knobs, and a checklist to validate real-world audio.

---

## What changed in code

- Twilio consent/disclosure prompts now support Arabic (TwiML `<Say language="ar-...">`) based on `CallSession.language` + `CallSession.country`.
- STT (Deepgram) supports **Arabic calls with optional dual streaming**:
  - Arabic stream: `ar` or `ar-<country>` where supported (e.g., `ar-EG`)
  - English stream: `en`
  - The runtime picks the “best” final transcript by confidence/length.
- TTS (ElevenLabs) supports **separate default voices** for English vs Arabic and chooses voice per chunk.

---

## Environment variables

### Twilio prompts (disclosure + consent)

- `VOICE_AI_DISCLOSURE_DEFAULT` (English default; optional)
- `VOICE_AI_DISCLOSURE_DEFAULT_AR` (Arabic default; optional)
- `VOICE_TWILIO_VOICE_EN` (optional; defaults to Twilio default voice)
- `VOICE_TWILIO_VOICE_AR` (optional; defaults to `Polly.Zeina`)

Note: Twilio voice availability depends on your Twilio account’s supported TTS voices.

### Deepgram (STT)

- `DEEPGRAM_API_KEY` (required)
- `DEEPGRAM_MODEL` (optional; default `nova-2`)
- `DEEPGRAM_ENDPOINTING_MS` (optional; default `300`)
- `VOICE_STT_DUAL_STREAM_AR_EN` (optional; default `true`)
  - When enabled and `CallSession.language=ar`, the runtime streams audio to both Arabic and English STT connections.
  - This improves Arabic↔English code-switch handling, but doubles STT usage for Arabic calls.

### ElevenLabs (TTS)

- `ELEVENLABS_API_KEY` (required)
- `ELEVENLABS_DEFAULT_VOICE_EN` (required unless `ELEVENLABS_VOICE_ID` is set)
- `ELEVENLABS_DEFAULT_VOICE_AR` (recommended for Arabic)
- `ELEVENLABS_VOICE_ID` (optional “force voice for all languages” override)
- `ELEVENLABS_MODEL_ID` or `ELEVENLABS_MODEL_ID_EN` / `ELEVENLABS_MODEL_ID_AR` (optional; default `eleven_flash_v2_5`)
- `ELEVENLABS_OUTPUT_FORMAT` (optional; default `ulaw_8000`)

---

## QA checklist (Egypt + GCC)

### 1) Arabic STT (Egyptian)

Test phrases (spoken naturally, fast + slow):

- “ألو، مين معايا؟”
- “تمام، ابعتلي التفاصيل على الواتساب.”
- “أنا مش فاكر رقم الطلب، ممكن تدور بالاسم؟”
- “مش هقدر دلوقتي، كلمني بكرة.”

### 2) Arabic STT (GCC)

- “هلا، من معاي؟”
- “ممكن ترسل التفاصيل على الإيميل؟”
- “أنا ما أذكر رقم الفاتورة.”
- “اتصل علي بعد شوي.”

### 3) Code-switch

- Start Arabic, then switch to English mid-sentence:
  - “تمام… one second, what was the total again?”
  - “أنا موافق بس can you confirm the date?”

Expected outcome:

- Transcript remains readable in both languages.
- The agent responds in the customer’s current language.

### 4) Consent drop-off

Measure:

- Hang-ups during the disclosure/consent prompt (per country).
- % who press 1 vs hang up.

---

## Operational notes

- If you see English being transcribed poorly on Arabic calls, keep `VOICE_STT_DUAL_STREAM_AR_EN=true`.
- If STT cost becomes an issue, disable dual streaming and require “Arabic-only” sessions for now.

