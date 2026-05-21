from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from apps.voice.models import CallSession
from apps.voice.providers.deepgram_stt import DeepgramConfig
from apps.voice.providers.deepgram_tts import DeepgramTTSConfig
from apps.voice.providers.elevenlabs_tts import ElevenLabsConfig
from apps.voice.runtime.call_flow import VoiceRuntimeCallFlowMixin
from apps.voice.runtime.context import VoiceRuntimeContextMixin
from apps.voice.runtime.events import VoiceRuntimeEventsMixin
from apps.voice.runtime.helpers import (
    _detect_text_language,
    _has_hangup_action,
    _merge_transcript_segments,
    _normalize_lang_for_prompt,
    _stt_language_tags_for_session,
    _stream_target_fields,
)
from apps.voice.runtime.response import VoiceRuntimeResponseMixin
from apps.voice.runtime.tts import VoiceRuntimeTTSMixin
from apps.voice.runtime.state import VoiceRuntimeStateMixin
from apps.voice.runtime.stream import VoiceRuntimeStreamMixin
from apps.voice.runtime.transcripts import VoiceRuntimeTranscriptMixin
from apps.voice.runtime.turn_taking import VoiceRuntimeTurnTakingMixin


logger = logging.getLogger(__name__)


@dataclass
class VoiceCallRuntimeConfig:
    max_history_turns: int = 8
    llm_timeout_s: float = 45.0


class VoiceCallRuntime(
    VoiceRuntimeTurnTakingMixin,
    VoiceRuntimeCallFlowMixin,
    VoiceRuntimeTTSMixin,
    VoiceRuntimeStateMixin,
    VoiceRuntimeEventsMixin,
    VoiceRuntimeContextMixin,
    VoiceRuntimeTranscriptMixin,
    VoiceRuntimeStreamMixin,
    VoiceRuntimeResponseMixin,
):
    """
    Production-shaped media-stream runtime (Twilio/Telnyx compatible).

    Phase 1 keeps this close to the Phase 0 spike while standardizing event
    types and emitting final assistant responses for post-call processing.
    """

    def __init__(self, *, session_id: str, runtime_config: VoiceCallRuntimeConfig | None = None) -> None:
        self.session_id = session_id
        self.runtime_config = runtime_config or VoiceCallRuntimeConfig()
        self._agent_response_seq: int = 0
        self._stream_sid: str | None = None
        self._history: list[tuple[str, str]] = []  # ("customer"|"agent", text)
        self._current_speak_task: asyncio.Task | None = None
        self._speaking_response_id: int = 0
        self._greeted: bool = False
        self._goal_delivered: bool = False
        self._last_handled_text: str = ""
        self._last_handled_at: float = 0.0
        self._context_loaded: bool = False
        self._context_block: str = ""
        self._session_cache: CallSession | None = None  # Cache session to avoid repeated DB queries
        self._pending_final_text: str = ""
        self._pending_final_confidence: float = 0.0
        self._pending_final_language: str = ""
        self._pending_final_updated_at: float = 0.0  # monotonic
        self._pending_final_task: asyncio.Task | None = None
        self._last_customer_activity_at: float = 0.0  # monotonic
        self._last_customer_final_at: float = 0.0  # monotonic
        self._last_agent_speech_end_at: float = 0.0  # monotonic
        self._customer_speaking: bool = False
        self._last_vad_speech_started_at: float = 0.0  # monotonic
        self._last_vad_utterance_end_at: float = 0.0  # monotonic
        self._awaiting_first_customer: bool = False
        self._no_engagement_task: asyncio.Task | None = None
        self._silence_close_task: asyncio.Task | None = None
        self._closing_waiting_for_customer: bool = False
        self._closing_question_asked_at: float = 0.0  # monotonic
        self._waiting_for_callback_time: bool = False
        self._callback_time_text: str = ""
        self._auto_resume_task: asyncio.Task | None = None
        self._auto_resume_text: str = ""
        self._auto_resume_response_id: int = 0
        self._auto_resume_marks_goal_delivered: bool = False
        self._auto_resume_expected_response_id: int = 0
        self._auto_resume_expected_barge_in_at: float = 0.0  # monotonic
        self._interrupted_agent_remaining: str = ""
        self._interrupted_agent_remaining_at: float = 0.0  # monotonic
        self._interrupted_agent_response_id: int = 0
        self._interrupted_agent_marks_goal_delivered: bool = False
        self._last_barge_in_at: float = 0.0  # monotonic
        self._last_barge_in_text: str = ""
        self._terminated: bool = False
        self._stt_config_cache: dict[str, DeepgramConfig] = {}
        self._tts_config_cache: dict[str, ElevenLabsConfig] = {}
        self._deepgram_tts_config_cache: dict[str, DeepgramTTSConfig] = {}
