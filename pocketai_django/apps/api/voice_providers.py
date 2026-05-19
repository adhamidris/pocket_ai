from __future__ import annotations

from apps.api.voice import provider_shared as _provider_shared
from apps.api.voice.providers import (
    voice_provider_detail,
    voice_provider_test,
    voice_providers_collection,
)

requests = _provider_shared.requests
