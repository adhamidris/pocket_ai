from __future__ import annotations

from asgiref.sync import sync_to_async

from apps.voice.providers.deepgram_stt import DeepgramConfig
from apps.voice.providers.deepgram_tts import DeepgramTTSConfig
from apps.voice.providers.elevenlabs_tts import ElevenLabsConfig
from apps.voice.models import CallSession
from apps.voice.providers.credentials import (
    VOICE_PROVIDER_TWILIO,
    resolve_deepgram_config,
    resolve_deepgram_tts_config,
    resolve_elevenlabs_config,
)


class VoiceRuntimeStateMixin:

    async def _get_session(self, *, force_refresh: bool = False) -> CallSession:
        """Get session with caching to reduce DB queries in critical path."""
        if self._session_cache is not None and not force_refresh:
            return self._session_cache
        session = await sync_to_async(CallSession.objects.get)(id=self.session_id)
        self._session_cache = session
        return session

    async def _get_deepgram_config(self, *, language: str) -> DeepgramConfig:
        lang = (language or "en").strip().lower() or "en"
        cached = self._stt_config_cache.get(lang)
        if cached is not None:
            return cached
        session = await self._get_session()
        config = await sync_to_async(resolve_deepgram_config)(
            business_id=session.business_profile_id,
            language=lang,
        )
        self._stt_config_cache[lang] = config
        return config

    async def _get_tts_config(self, *, language: str | None) -> ElevenLabsConfig:
        lang = (language or "en").strip().lower() or "en"
        cached = self._tts_config_cache.get(lang)
        if cached is not None:
            return cached
        session = await self._get_session()
        config = await sync_to_async(resolve_elevenlabs_config)(
            business_id=session.business_profile_id,
            language=lang,
        )
        self._tts_config_cache[lang] = config
        return config

    async def _get_deepgram_tts_config(self, *, language: str | None) -> DeepgramTTSConfig:
        lang = (language or "en").strip().lower() or "en"
        cached = self._deepgram_tts_config_cache.get(lang)
        if cached is not None:
            return cached
        session = await self._get_session()
        config = await sync_to_async(resolve_deepgram_tts_config)(
            business_id=session.business_profile_id,
            language=lang,
        )
        self._deepgram_tts_config_cache[lang] = config
        return config

    async def _update_stream_ids(self, *, stream_sid: str, call_sid: str) -> None:
        def _update() -> None:
            session = CallSession.objects.filter(id=self.session_id).first()
            if not session:
                return

            updates: list[str] = []
            if stream_sid and stream_sid != str(session.provider_stream_sid or ""):
                session.provider_stream_sid = stream_sid
                updates.append("provider_stream_sid")
            if call_sid and call_sid != str(session.provider_call_sid or ""):
                session.provider_call_sid = call_sid
                updates.append("provider_call_sid")

            provider = str(session.transport_provider or "").strip().lower()
            if provider in {"", VOICE_PROVIDER_TWILIO}:
                if stream_sid and stream_sid != str(session.twilio_stream_sid or ""):
                    session.twilio_stream_sid = stream_sid
                    updates.append("twilio_stream_sid")
                if call_sid and call_sid != str(session.twilio_call_sid or ""):
                    session.twilio_call_sid = call_sid
                    updates.append("twilio_call_sid")

            if not provider and call_sid.startswith("CA"):
                session.transport_provider = VOICE_PROVIDER_TWILIO
                updates.append("transport_provider")

            if updates:
                updates.append("updated_at")
                session.save(update_fields=updates)

        await sync_to_async(_update)()
