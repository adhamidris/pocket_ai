from __future__ import annotations

from typing import Any, Mapping

from core.tenancy import tenant_context

from apps.voice.deepgram_stt import DeepgramConfig
from apps.voice.elevenlabs_tts import ElevenLabsConfig
from apps.voice.models import VoiceProviderConnection
from apps.voice.twilio import TwilioConfig, load_twilio_config


VOICE_PROVIDER_TWILIO = VoiceProviderConnection.Provider.TWILIO
VOICE_PROVIDER_DEEPGRAM = VoiceProviderConnection.Provider.DEEPGRAM
VOICE_PROVIDER_ELEVENLABS = VoiceProviderConnection.Provider.ELEVENLABS

VOICE_PROVIDER_ORDER = (
    VOICE_PROVIDER_TWILIO,
    VOICE_PROVIDER_DEEPGRAM,
    VOICE_PROVIDER_ELEVENLABS,
)
VOICE_PROVIDER_TENANT_MANAGED = (VOICE_PROVIDER_TWILIO,)
VOICE_PROVIDER_PLATFORM_MANAGED = (
    VOICE_PROVIDER_DEEPGRAM,
    VOICE_PROVIDER_ELEVENLABS,
)


def is_tenant_managed_provider(provider: str) -> bool:
    return str(provider or "").strip().lower() in VOICE_PROVIDER_TENANT_MANAGED


def is_platform_managed_provider(provider: str) -> bool:
    return str(provider or "").strip().lower() in VOICE_PROVIDER_PLATFORM_MANAGED


def get_provider_connection(*, business_id: Any | None, provider: str) -> VoiceProviderConnection | None:
    if not business_id:
        return None
    with tenant_context(business_id):
        return VoiceProviderConnection.objects.filter(
            business_profile_id=business_id,
            provider=str(provider).strip().lower(),
        ).first()


def resolve_twilio_config(*, business_id: Any | None, require_from_number: bool = True) -> TwilioConfig:
    if not business_id:
        raise ValueError("Twilio provider requires a workspace context.")
    connection = get_provider_connection(business_id=business_id, provider=VOICE_PROVIDER_TWILIO)
    if connection is None:
        raise ValueError("Twilio provider is not configured for this workspace.")
    if not connection.enabled:
        raise ValueError("Twilio provider is disabled for this workspace.")
    credentials = connection.credentials or {}
    if not credentials:
        raise ValueError("Twilio provider credentials are missing for this workspace.")
    return load_twilio_config(
        require_from_number=require_from_number,
        overrides=credentials,
        allow_env_fallback=False,
    )


def resolve_deepgram_config(*, business_id: Any | None, language: str) -> DeepgramConfig:
    # Platform-managed provider: always use owner-managed credentials.
    return DeepgramConfig.from_env(language=language)


def resolve_elevenlabs_config(*, business_id: Any | None, language: str | None = None) -> ElevenLabsConfig:
    # Platform-managed provider: always use owner-managed credentials.
    return ElevenLabsConfig.from_env(language=language)


def extract_safe_provider_settings(provider: str, credentials: Mapping[str, Any]) -> dict[str, Any]:
    provider_key = str(provider or "").strip().lower()
    source = dict(credentials or {})
    if provider_key == VOICE_PROVIDER_TWILIO:
        return {
            "account_sid": str(source.get("account_sid") or "").strip(),
            "webhook_base_url": str(source.get("webhook_base_url") or "").strip().rstrip("/"),
            "from_number": str(source.get("from_number") or "").strip(),
        }
    if provider_key == VOICE_PROVIDER_DEEPGRAM:
        endpointing_ms = source.get("endpointing_ms")
        try:
            endpointing = int(endpointing_ms)
        except Exception:
            endpointing = None
        return {
            "model": str(source.get("model") or "").strip(),
            "endpointing_ms": endpointing,
        }
    if provider_key == VOICE_PROVIDER_ELEVENLABS:
        return {
            "voice_id": str(source.get("voice_id") or "").strip(),
            "default_voice_en": str(source.get("default_voice_en") or "").strip(),
            "default_voice_ar": str(source.get("default_voice_ar") or "").strip(),
            "model_id": str(source.get("model_id") or "").strip(),
            "output_format": str(source.get("output_format") or "").strip(),
        }
    return {}


def mask_provider_credentials(provider: str, credentials: Mapping[str, Any]) -> dict[str, Any]:
    provider_key = str(provider or "").strip().lower()
    source = dict(credentials or {})

    def _mask_tail(value: str, tail: int = 4) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) <= tail:
            return "*" * len(text)
        return ("*" * (len(text) - tail)) + text[-tail:]

    if provider_key == VOICE_PROVIDER_TWILIO:
        return {
            "auth_token": _mask_tail(str(source.get("auth_token") or ""), tail=4),
        }
    if provider_key == VOICE_PROVIDER_DEEPGRAM:
        return {
            "api_key": _mask_tail(str(source.get("api_key") or ""), tail=4),
        }
    if provider_key == VOICE_PROVIDER_ELEVENLABS:
        return {
            "api_key": _mask_tail(str(source.get("api_key") or ""), tail=4),
        }
    return {}
