from __future__ import annotations

import dataclasses
import logging
from contextlib import nullcontext
from typing import Mapping, Sequence

from django.db import transaction

from apps.accounts.constants import (
    FEATURE_FLAG_DEFAULTS,
    FEATURE_FLAG_METADATA_KEY,
    coerce_feature_value,
    sanitize_feature_payload,
)
from apps.accounts.models import BusinessProfile

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class FeatureState:
    alias_lookup: bool
    entity_chunking: bool
    hybrid_search: bool
    rag_chunk_quality_filter: bool
    rag_chunk_dedupe: bool
    rag_alias_hygiene: bool
    rag_text_chunk_penalty: bool
    rag_shadow_ingestion: bool
    rag_shadow_retrieval: bool
    rag_eval_logging: bool
    rag_agentic_mode: bool  # 2-tool retrieval: search (metadata) → read (content)
    mcp_gateway_mode: bool  # Small gateway tool surface for external MCP

    def as_dict(self) -> dict[str, bool]:
        return {
            "alias_lookup": self.alias_lookup,
            "entity_chunking": self.entity_chunking,
            "hybrid_search": self.hybrid_search,
            "rag_chunk_quality_filter": self.rag_chunk_quality_filter,
            "rag_chunk_dedupe": self.rag_chunk_dedupe,
            "rag_alias_hygiene": self.rag_alias_hygiene,
            "rag_text_chunk_penalty": self.rag_text_chunk_penalty,
            "rag_shadow_ingestion": self.rag_shadow_ingestion,
            "rag_shadow_retrieval": self.rag_shadow_retrieval,
            "rag_eval_logging": self.rag_eval_logging,
            "rag_agentic_mode": self.rag_agentic_mode,
            "mcp_gateway_mode": self.mcp_gateway_mode,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class FeatureRolloutResult:
    business_id: str
    business_name: str
    before: FeatureState
    after: FeatureState
    changed: bool


class FeatureFlagService:
    """Helpers for reading and mutating per-business knowledge feature flags."""

    ALL_FLAGS: tuple[str, ...] = tuple(FEATURE_FLAG_DEFAULTS.keys())

    @classmethod
    def snapshot(cls, business_profile: BusinessProfile | None) -> FeatureState:
        metadata = getattr(business_profile, "metadata", {}) if business_profile else {}
        payload = metadata.get(FEATURE_FLAG_METADATA_KEY) if isinstance(metadata, dict) else {}
        normalized = sanitize_feature_payload(payload)
        return cls._state_from_payload(normalized)

    @classmethod
    def _state_from_payload(cls, payload: Mapping[str, bool]) -> FeatureState:
        return FeatureState(
            alias_lookup=bool(payload.get("alias_lookup", FEATURE_FLAG_DEFAULTS["alias_lookup"])),
            entity_chunking=bool(payload.get("entity_chunking", FEATURE_FLAG_DEFAULTS["entity_chunking"])),
            hybrid_search=bool(payload.get("hybrid_search", FEATURE_FLAG_DEFAULTS["hybrid_search"])),
            rag_chunk_quality_filter=bool(
                payload.get("rag_chunk_quality_filter", FEATURE_FLAG_DEFAULTS["rag_chunk_quality_filter"])
            ),
            rag_chunk_dedupe=bool(payload.get("rag_chunk_dedupe", FEATURE_FLAG_DEFAULTS["rag_chunk_dedupe"])),
            rag_alias_hygiene=bool(payload.get("rag_alias_hygiene", FEATURE_FLAG_DEFAULTS["rag_alias_hygiene"])),
            rag_text_chunk_penalty=bool(
                payload.get("rag_text_chunk_penalty", FEATURE_FLAG_DEFAULTS["rag_text_chunk_penalty"])
            ),
            rag_shadow_ingestion=bool(
                payload.get("rag_shadow_ingestion", FEATURE_FLAG_DEFAULTS["rag_shadow_ingestion"])
            ),
            rag_shadow_retrieval=bool(
                payload.get("rag_shadow_retrieval", FEATURE_FLAG_DEFAULTS["rag_shadow_retrieval"])
            ),
            rag_eval_logging=bool(payload.get("rag_eval_logging", FEATURE_FLAG_DEFAULTS["rag_eval_logging"])),
            rag_agentic_mode=bool(payload.get("rag_agentic_mode", FEATURE_FLAG_DEFAULTS["rag_agentic_mode"])),
            mcp_gateway_mode=bool(payload.get("mcp_gateway_mode", FEATURE_FLAG_DEFAULTS["mcp_gateway_mode"])),
        )

    @classmethod
    def set_flags(
        cls,
        business_profile: BusinessProfile,
        *,
        updates: Mapping[str, object],
        commit: bool = True,
    ) -> FeatureState:
        metadata = business_profile.metadata if isinstance(business_profile.metadata, dict) else {}
        normalized = sanitize_feature_payload(metadata.get(FEATURE_FLAG_METADATA_KEY))
        changed = False
        for name, value in updates.items():
            if name not in cls.ALL_FLAGS:
                logger.warning("feature.flags.unknown business=%s flag=%s", business_profile.id, name)
                continue
            normalized_value = coerce_feature_value(value, normalized.get(name, FEATURE_FLAG_DEFAULTS[name]))
            if normalized.get(name) != normalized_value:
                normalized[name] = normalized_value
                changed = True
        if changed:
            metadata_copy = dict(metadata) if isinstance(metadata, dict) else {}
            metadata_copy[FEATURE_FLAG_METADATA_KEY] = normalized
            business_profile.metadata = metadata_copy
            if commit:
                business_profile.save(update_fields=["metadata", "updated_at"])
                logger.info(
                    "feature.flags.updated business=%s state=%s",
                    business_profile.id,
                    {name: normalized.get(name) for name in cls.ALL_FLAGS},
                )
        return cls._state_from_payload(normalized)

    @classmethod
    def bulk_apply(
        cls,
        queryset,
        *,
        enable: Sequence[str] | None = None,
        disable: Sequence[str] | None = None,
        dry_run: bool = False,
    ) -> list[FeatureRolloutResult]:
        enable = tuple(enable or ())
        disable = tuple(disable or ())
        invalid = [flag for flag in (*enable, *disable) if flag not in cls.ALL_FLAGS]
        if invalid:
            raise ValueError(f"Unsupported feature flag(s): {', '.join(sorted(set(invalid)))}")
        updates = {flag: True for flag in enable}
        updates.update({flag: False for flag in disable})
        results: list[FeatureRolloutResult] = []
        if not updates:
            for business in queryset.iterator():
                state = cls.snapshot(business)
                results.append(
                    FeatureRolloutResult(
                        business_id=str(business.id),
                        business_name=business.name,
                        before=state,
                        after=state,
                        changed=False,
                    )
                )
            return results

        context = nullcontext()
        iterator = queryset.iterator() if dry_run else queryset.select_for_update().iterator()
        if not dry_run:
            context = transaction.atomic()
        with context:
            for business in iterator:
                before = cls.snapshot(business)
                after = cls.set_flags(business, updates=updates, commit=not dry_run)
                results.append(
                    FeatureRolloutResult(
                        business_id=str(business.id),
                        business_name=business.name,
                        before=before,
                        after=after,
                        changed=before.as_dict() != after.as_dict(),
                    )
                )
        return results

    @classmethod
    def describe_flags(cls) -> dict[str, bool]:
        return dict(FEATURE_FLAG_DEFAULTS)


__all__ = ["FeatureFlagService", "FeatureState", "FeatureRolloutResult"]
