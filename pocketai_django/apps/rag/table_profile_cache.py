"""
Redis cache helpers for table profile aggregation.

Precomputes table context (columns, profiles, row labels) at ingestion-time
and caches in Redis to avoid 0.5-2s query penalty on every search.
"""
import uuid
from typing import Mapping

from django.core.cache import cache
from django.conf import settings


def _table_profile_version_key(business_id: uuid.UUID) -> str:
    return f"table_profile:ver:{business_id}"


def _get_table_profile_version(business_id: uuid.UUID) -> int:
    version = cache.get(_table_profile_version_key(business_id))
    if version is None:
        cache.set(_table_profile_version_key(business_id), 0, None)
        return 0
    try:
        return int(version)
    except (TypeError, ValueError):
        cache.set(_table_profile_version_key(business_id), 0, None)
        return 0


def _table_profile_cache_key(
    business_id: uuid.UUID,
    *,
    version: int | None = None,
) -> str:
    """
    Generate Redis cache key for table profile.
    
    Format: table_profile:{business_id}:v{version}:all
    """
    version = _get_table_profile_version(business_id) if version is None else int(version)
    return f"table_profile:{business_id}:v{version}:all"


def get_table_profile_cache(
    business_id: uuid.UUID,
) -> Mapping[str, object] | None:
    """
    Fetch cached table profile from Redis.
    
    Returns None if cache miss.
    """
    key = _table_profile_cache_key(business_id)
    cached = cache.get(key)
    if cached and isinstance(cached, (dict, Mapping)):
        return dict(cached)
    return None


def set_table_profile_cache(
    business_id: uuid.UUID,
    profile: Mapping[str, object],
    *,
    ttl_seconds: int | None = None,
) -> None:
    """
    Store table profile in Redis cache.
    
    Args:
        business_id: Tenant ID
        profile: Aggregated table profile with keys:
            - available_columns: set of column names
            - row_label_tokens: set of row label tokens
            - table_count: total number of tables
            - table_uploads: number of uploads with tables
            - table_upload_ratio: ratio of table uploads
            - dominant: bool indicating if tables dominant
        ttl_seconds: Cache TTL (default: 15 minutes)
    """
    if ttl_seconds is None:
        ttl_seconds = int(getattr(settings, "TABLE_PROFILE_CACHE_TTL", 900))  # 15 min
    
    key = _table_profile_cache_key(business_id)
    
    # Convert sets to lists for JSON serialization
    serializable_profile = dict(profile)
    if "available_columns" in serializable_profile and isinstance(serializable_profile["available_columns"], set):
        serializable_profile["available_columns"] = sorted(serializable_profile["available_columns"])
    if "row_label_tokens" in serializable_profile and isinstance(serializable_profile["row_label_tokens"], set):
        serializable_profile["row_label_tokens"] = sorted(serializable_profile["row_label_tokens"])
    
    cache.set(key, serializable_profile, timeout=ttl_seconds)


def invalidate_table_profile_cache(
    business_id: uuid.UUID,
) -> None:
    """
    Invalidate cached table profile.
    
    Call this when tables are uploaded/deleted to force recomputation.
    """
    # Version bump invalidates ALL collection-scoped variants without needing to enumerate keys.
    version_key = _table_profile_version_key(business_id)
    try:
        cache.incr(version_key)
    except ValueError:
        cache.set(version_key, 1, None)


def invalidate_all_table_profiles(business_id: uuid.UUID) -> None:
    """
    Invalidate ALL table profile caches for a tenant.
    
    Use this for bulk operations (e.g., delete all uploads).
    """
    invalidate_table_profile_cache(business_id)
