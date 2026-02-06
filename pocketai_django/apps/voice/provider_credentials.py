from __future__ import annotations

from typing import Any, Mapping

from core.tenancy import tenant_context

from apps.voice.deepgram_stt import DeepgramConfig
from apps.voice.deepgram_tts import DeepgramTTSConfig
from apps.voice.elevenlabs_tts import ElevenLabsConfig
from apps.voice.models import VoiceConfiguration, VoiceProviderConnection
from apps.voice.telnyx import TelnyxConfig, load_telnyx_config
from apps.voice.twilio import TwilioConfig, load_twilio_config


VOICE_PROVIDER_TWILIO = VoiceProviderConnection.Provider.TWILIO
VOICE_PROVIDER_TELNYX = VoiceProviderConnection.Provider.TELNYX
VOICE_PROVIDER_DEEPGRAM = VoiceProviderConnection.Provider.DEEPGRAM
VOICE_PROVIDER_ELEVENLABS = VoiceProviderConnection.Provider.ELEVENLABS

VOICE_PROVIDER_ORDER = (
    VOICE_PROVIDER_TWILIO,
    VOICE_PROVIDER_TELNYX,
    VOICE_PROVIDER_DEEPGRAM,
    VOICE_PROVIDER_ELEVENLABS,
)
VOICE_PROVIDER_TENANT_MANAGED = (
    VOICE_PROVIDER_TWILIO,
    VOICE_PROVIDER_TELNYX,
)
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


def list_configured_tenant_providers(*, business_id: Any | None) -> list[str]:
    if not business_id:
        return []
    with tenant_context(business_id):
        configured: list[str] = []
        for row in VoiceProviderConnection.objects.filter(
            business_profile_id=business_id,
            provider__in=VOICE_PROVIDER_TENANT_MANAGED,
            enabled=True,
        ).only("provider", "credentials_encrypted"):
            try:
                if row.has_credentials():
                    configured.append(str(row.provider or "").strip().lower())
            except Exception:
                continue
        return configured


def resolve_active_transport_provider(*, business_id: Any | None) -> str:
    """
    Resolve outbound transport provider for a workspace.

    No provider-specific fallback is used. Behavior:
    - If workspace selected provider is valid/configured, return it.
    - If no explicit selection and exactly one provider is configured, return it.
    - Otherwise raise a clear error requiring setup/selection.
    """

    if not business_id:
        raise ValueError("Voice transport provider requires a workspace context.")

    with tenant_context(business_id):
        config = VoiceConfiguration.objects.filter(business_profile_id=business_id).only("active_transport_provider").first()
        preferred = str(getattr(config, "active_transport_provider", "") or "").strip().lower()
        configured = list_configured_tenant_providers(business_id=business_id)
        configured_set = {item for item in configured if item in VOICE_PROVIDER_TENANT_MANAGED}

        if preferred:
            if preferred not in VOICE_PROVIDER_TENANT_MANAGED:
                raise ValueError("Selected voice transport provider is unsupported.")
            if preferred not in configured_set:
                raise ValueError("Selected voice transport provider is not configured/enabled.")
            return preferred

        if len(configured_set) == 1:
            return next(iter(configured_set))
        if len(configured_set) == 0:
            raise ValueError("No outbound voice transport provider is configured.")
        raise ValueError("Multiple outbound voice providers are configured; select an active transport provider.")


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


def resolve_telnyx_config(*, business_id: Any | None, require_from_number: bool = True) -> TelnyxConfig:
    if not business_id:
        raise ValueError("Telnyx provider requires a workspace context.")
    connection = get_provider_connection(business_id=business_id, provider=VOICE_PROVIDER_TELNYX)
    if connection is None:
        raise ValueError("Telnyx provider is not configured for this workspace.")
    if not connection.enabled:
        raise ValueError("Telnyx provider is disabled for this workspace.")
    credentials = connection.credentials or {}
    if not credentials:
        raise ValueError("Telnyx provider credentials are missing for this workspace.")
    return load_telnyx_config(
        require_from_number=require_from_number,
        overrides=credentials,
        allow_env_fallback=False,
    )


def resolve_transport_config(*, business_id: Any | None, provider: str, require_from_number: bool = True) -> TwilioConfig | TelnyxConfig:
    provider_key = str(provider or "").strip().lower()
    if provider_key == VOICE_PROVIDER_TWILIO:
        return resolve_twilio_config(business_id=business_id, require_from_number=require_from_number)
    if provider_key == VOICE_PROVIDER_TELNYX:
        return resolve_telnyx_config(business_id=business_id, require_from_number=require_from_number)
    raise ValueError("Unsupported voice transport provider.")


def resolve_deepgram_config(*, business_id: Any | None, language: str) -> DeepgramConfig:
    # Platform-managed provider: always use owner-managed credentials.
    return DeepgramConfig.from_env(language=language)


def resolve_elevenlabs_config(*, business_id: Any | None, language: str | None = None) -> ElevenLabsConfig:
    # Platform-managed provider: always use owner-managed credentials.
    return ElevenLabsConfig.from_env(language=language)


def resolve_deepgram_tts_config(*, business_id: Any | None, language: str | None = None) -> DeepgramTTSConfig:
    # Platform-managed provider: always use owner-managed credentials.
    return DeepgramTTSConfig.from_env()


def extract_safe_provider_settings(provider: str, credentials: Mapping[str, Any]) -> dict[str, Any]:
    provider_key = str(provider or "").strip().lower()
    source = dict(credentials or {})
    if provider_key == VOICE_PROVIDER_TWILIO:
        return {
            "account_sid": str(source.get("account_sid") or "").strip(),
            "webhook_base_url": str(source.get("webhook_base_url") or "").strip().rstrip("/"),
            "from_number": str(source.get("from_number") or "").strip(),
        }
    if provider_key == VOICE_PROVIDER_TELNYX:
        return {
            "account_sid": str(source.get("account_sid") or "").strip(),
            "application_sid": str(source.get("application_sid") or source.get("app_sid") or "").strip(),
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
    if provider_key == VOICE_PROVIDER_TELNYX:
        return {
            "api_key": _mask_tail(str(source.get("api_key") or ""), tail=4),
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
