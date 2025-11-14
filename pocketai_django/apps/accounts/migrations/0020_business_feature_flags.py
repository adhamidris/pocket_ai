from __future__ import annotations

from django.db import migrations

FEATURE_FLAG_METADATA_KEY = "features"
FEATURE_FLAG_DEFAULTS = {
    "alias_lookup": True,
    "entity_chunking": True,
    "hybrid_search": True,
}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled", ""}


def _coerce(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    return bool(default)


def _sanitize(payload):
    sanitized = {}
    if isinstance(payload, dict):
        for key, raw in payload.items():
            if key in FEATURE_FLAG_DEFAULTS:
                sanitized[key] = _coerce(raw, FEATURE_FLAG_DEFAULTS[key])
    for key, default in FEATURE_FLAG_DEFAULTS.items():
        sanitized.setdefault(key, bool(default))
    return sanitized


def apply_feature_defaults(apps, schema_editor):
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    for business in BusinessProfile.objects.all().iterator():
        metadata = business.metadata if isinstance(business.metadata, dict) else {}
        current = metadata.get(FEATURE_FLAG_METADATA_KEY)
        normalized = _sanitize(current)
        if metadata.get(FEATURE_FLAG_METADATA_KEY) == normalized:
            continue
        updated = dict(metadata) if isinstance(metadata, dict) else {}
        updated[FEATURE_FLAG_METADATA_KEY] = normalized
        BusinessProfile.objects.filter(pk=business.pk).update(metadata=updated)


def noop_reverse(apps, schema_editor):  # pragma: no cover - required signature
    return


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_knowledgedriftsample_knowledgefeedbackcase_and_more"),
    ]

    operations = [
        migrations.RunPython(apply_feature_defaults, noop_reverse),
    ]
