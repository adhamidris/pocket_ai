"""Helpers for encrypting/decrypting integration credentials with tenant scoping."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

logger = logging.getLogger(__name__)


class IntegrationSecretError(RuntimeError):
    """Raised when credentials cannot be decrypted or validated."""


@dataclass(frozen=True)
class CredentialPolicy:
    rotation_days: int
    error_threshold: int


class IntegrationSecretManager:
    """Encrypts credential payloads using the configured Fernet key."""

    def __init__(self, raw_key: str | bytes, *, key_version: int = 1):
        if not raw_key:
            raise ImproperlyConfigured("INTEGRATION_CREDENTIALS_KEY is required for credential encryption.")
        if isinstance(raw_key, str):
            raw_key = raw_key.encode("utf-8")
        self._fernet = Fernet(raw_key)
        self.key_version = key_version

    def encrypt(self, payload: Dict[str, Any] | None, *, tenant: str) -> str:
        envelope = {
            "tenant": tenant,
            "payload": payload or {},
            "rotated_at": timezone.now().isoformat(),
        }
        blob = json.dumps(envelope).encode("utf-8")
        token = self._fernet.encrypt(blob)
        return token.decode("utf-8")

    def decrypt(self, ciphertext: str | bytes, *, tenant: str) -> dict[str, Any]:
        if not ciphertext:
            return {}
        if isinstance(ciphertext, str):
            ciphertext = ciphertext.encode("utf-8")
        try:
            data = self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:  # pragma: no cover
            raise IntegrationSecretError("Unable to decrypt credentials blob.") from exc
        try:
            envelope = json.loads(data.decode("utf-8"))
        except ValueError as exc:  # pragma: no cover
            raise IntegrationSecretError("Credentials payload corrupted.") from exc

        stored_tenant = envelope.get("tenant")
        if stored_tenant and tenant and stored_tenant != tenant:
            raise IntegrationSecretError("Credential tenant mismatch.")

        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return {}
        return payload


_default_manager: IntegrationSecretManager | None = None


def get_secret_manager() -> IntegrationSecretManager:
    global _default_manager
    if _default_manager is not None:
        return _default_manager

    key = getattr(settings, "INTEGRATION_CREDENTIALS_KEY", "")
    if not key:
        raise ImproperlyConfigured("INTEGRATION_CREDENTIALS_KEY must be configured before storing credentials.")
    _default_manager = IntegrationSecretManager(key)
    return _default_manager


def credential_policy() -> CredentialPolicy:
    return CredentialPolicy(
        rotation_days=int(getattr(settings, "INTEGRATION_CREDENTIAL_ROTATION_DAYS", 30)),
        error_threshold=int(getattr(settings, "INTEGRATION_CREDENTIAL_MAX_ERRORS", 3)),
    )


def credentials_are_stale(last_rotated_at: datetime | None, *, now: datetime | None = None) -> bool:
    if last_rotated_at is None:
        return False
    policy = credential_policy()
    reference = now or timezone.now()
    return last_rotated_at + timedelta(days=policy.rotation_days) <= reference
