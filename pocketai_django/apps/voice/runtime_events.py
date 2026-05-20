from __future__ import annotations

import asyncio
import logging
import time

from asgiref.sync import sync_to_async

from apps.voice.models import CallSession

logger = logging.getLogger(__name__)


class VoiceRuntimeEventsMixin:

    async def _log_event(self, event_type: str, payload: dict, *, blocking: bool = False) -> None:
        """
        Log call event to database.

        By default, runs in fire-and-forget mode to avoid blocking the critical path.
        Set blocking=True for events that must complete before proceeding.
        """
        session_id = self.session_id
        conversation_id = None
        if self._session_cache:
            conversation_id = getattr(self._session_cache, "initiating_conversation_id", None)

        def _create() -> CallSession | None:
            try:
                session = CallSession.objects.get(id=session_id)
                session.events.create(event_type=event_type, payload=payload)
                return session
            except Exception:
                logger.exception("Failed to log event %s", event_type)
                return None

        if blocking:
            session = await sync_to_async(_create)()
            if session and event_type in ("stt.final", "llm.response.final"):
                await self._broadcast_transcript_sse(session, event_type, payload)
        else:
            # Fire-and-forget: don't block the critical path
            async def _log_async() -> None:
                session = await sync_to_async(_create)()
                if session and event_type in ("stt.final", "llm.response.final"):
                    # Broadcast to SSE (also fire-and-forget)
                    asyncio.create_task(self._broadcast_transcript_sse(session, event_type, payload))

            asyncio.create_task(_log_async())

    async def _broadcast_transcript_sse(self, session: CallSession, event_type: str, payload: dict) -> None:
        """Push transcript events to cache for SSE polling."""
        from django.core.cache import cache

        conversation_id = getattr(session, "initiating_conversation_id", None)
        if not conversation_id:
            return

        role = "customer" if event_type == "stt.final" else "agent"
        text = str(payload.get("text") or "").strip()
        if not text:
            return

        cache_key = f"voice_transcript:{conversation_id}"
        event_data = {
            "session_id": str(session.id),
            "event_type": event_type,
            "role": role,
            "text": text,
            "timestamp": time.time(),
        }

        # Phase 4: Prefer publishing transcript updates to the portal session Redis event bus.
        # Keep cache fallback for the legacy DB-polling session stream.
        try:
            from apps.conversations.portal_session_event_bus import publish_portal_conversation_event

            publish_portal_conversation_event(
                conversation_id=conversation_id,
                event_name="voiceCallTranscript",
                payload=event_data,
            )
        except Exception:
            pass

        def _push_to_cache() -> None:
            existing = cache.get(cache_key) or []
            if not isinstance(existing, list):
                existing = []
            existing.append(event_data)
            # Keep only recent events (last 100)
            if len(existing) > 100:
                existing = existing[-100:]
            cache.set(cache_key, existing, timeout=300)  # 5 minute TTL

        await sync_to_async(_push_to_cache)()
