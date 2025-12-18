from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from django.conf import settings

from apps.accounts.models import KnowledgeStatus, KnowledgeUpload, KnowledgeVisibility
from apps.services.knowledge_access import apply_customer_visible_uploads
from apps.services.dataset_key_index import BloomFilter, normalize_identifier_value

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatasetKeyIndexHit:
    upload_id: str
    upload_name: str
    sheet_name: str | None
    sheet_index: int | None
    column: str
    identifier_key: str | None
    identifier_required: bool | None
    source: str | None


_BLOOM_CACHE: dict[str, tuple[float, BloomFilter]] = {}


def _cache_size_limit() -> int:
    try:
        value = int(getattr(settings, "DATASET_KEY_INDEX_CACHE_SIZE", 128) or 128)
    except (TypeError, ValueError):
        value = 128
    return max(8, min(2048, value))


def _load_bloom(*, storage_path: str, bits: int, hashes: int) -> BloomFilter | None:
    if not storage_path or bits <= 0 or hashes <= 0:
        return None
    media_root = Path(getattr(settings, "MEDIA_ROOT", ".")).resolve()
    abs_path = (media_root / Path(storage_path)).resolve()
    try:
        abs_path.relative_to(media_root)
    except ValueError:
        return None
    try:
        stat = abs_path.stat()
    except OSError:
        return None
    cache_key = f"{storage_path}:{bits}:{hashes}"
    cached = _BLOOM_CACHE.get(cache_key)
    if cached and cached[0] == stat.st_mtime:
        return cached[1]
    try:
        data = abs_path.read_bytes()
    except OSError:
        return None
    try:
        bloom = BloomFilter.from_bytes(bits=bits, hashes=hashes, data=data)
    except Exception:
        return None
    _BLOOM_CACHE[cache_key] = (stat.st_mtime, bloom)
    limit = _cache_size_limit()
    if len(_BLOOM_CACHE) > limit:
        for evict_key in list(_BLOOM_CACHE.keys())[: max(1, len(_BLOOM_CACHE) - limit)]:
            _BLOOM_CACHE.pop(evict_key, None)
    return bloom


def _iter_upload_key_indexes(upload: KnowledgeUpload) -> Sequence[dict[str, Any]]:
    meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
    dataset = meta.get("dataset") if isinstance(meta, Mapping) else None
    if not isinstance(dataset, Mapping) or not dataset.get("enabled"):
        return []
    sheets = dataset.get("sheets")
    indexes: list[dict[str, Any]] = []
    if isinstance(sheets, list) and sheets:
        for sheet in sheets:
            if not isinstance(sheet, Mapping):
                continue
            sheet_name = str(sheet.get("sheet_name") or "").strip() or None
            try:
                sheet_index = int(sheet.get("sheet_index")) if sheet.get("sheet_index") is not None else None
            except (TypeError, ValueError):
                sheet_index = None
            key_indexes = sheet.get("key_indexes")
            if not isinstance(key_indexes, list):
                continue
            for entry in key_indexes:
                if not isinstance(entry, Mapping):
                    continue
                indexes.append(
                    {
                        **dict(entry),
                        "sheet_name": sheet_name,
                        "sheet_index": sheet_index,
                    }
                )
        return indexes
    key_indexes = dataset.get("key_indexes")
    if isinstance(key_indexes, list):
        for entry in key_indexes:
            if not isinstance(entry, Mapping):
                continue
            indexes.append({**dict(entry), "sheet_name": None, "sheet_index": None})
    return indexes


def find_datasets_for_identifier(
    *,
    business_profile,
    identifier_value: str,
    max_hits: int | None = None,
) -> list[DatasetKeyIndexHit]:
    normalized = normalize_identifier_value(identifier_value)
    if not normalized:
        return []
    if max_hits is None:
        try:
            max_hits = int(getattr(settings, "DATASET_KEY_INDEX_MAX_MATCHES", 8) or 8)
        except (TypeError, ValueError):
            max_hits = 8
    max_hits = max(1, min(50, int(max_hits)))

    uploads = apply_customer_visible_uploads(
        KnowledgeUpload.objects.filter(
            business_profile=business_profile,
            status=KnowledgeStatus.ACTIVE,
            ingestion_metadata__dataset__enabled=True,
        )
    ).only("id", "display_name", "filename", "ingestion_metadata")

    hits: list[DatasetKeyIndexHit] = []
    for upload in uploads:
        upload_name = (upload.display_name or upload.filename or str(upload.id)).strip()
        for entry in _iter_upload_key_indexes(upload):
            storage_path = str(entry.get("storage_path") or "").strip()
            try:
                bits = int(entry.get("bits") or 0)
                hashes = int(entry.get("hashes") or 0)
            except (TypeError, ValueError):
                continue
            bloom = _load_bloom(storage_path=storage_path, bits=bits, hashes=hashes)
            if not bloom:
                continue
            if not bloom.maybe_contains(normalized):
                continue
            column = str(entry.get("column") or "").strip() or "key"
            identifier_key = str(entry.get("identifier_key") or "").strip() or None
            identifier_required = entry.get("identifier_required")
            if identifier_required is not None:
                identifier_required = bool(identifier_required)
            sheet_name = str(entry.get("sheet_name") or "").strip() or None
            try:
                sheet_index = int(entry.get("sheet_index")) if entry.get("sheet_index") is not None else None
            except (TypeError, ValueError):
                sheet_index = None
            hits.append(
                DatasetKeyIndexHit(
                    upload_id=str(upload.id),
                    upload_name=upload_name,
                    sheet_name=sheet_name,
                    sheet_index=sheet_index,
                    column=column,
                    identifier_key=identifier_key,
                    identifier_required=identifier_required,
                    source=str(entry.get("source") or "").strip() or None,
                )
            )
            if len(hits) >= max_hits:
                return hits
    return hits


def match_upload_for_identifier(
    *,
    upload: KnowledgeUpload,
    identifier_value: str,
    max_hits: int | None = None,
) -> list[DatasetKeyIndexHit]:
    if getattr(upload, "visibility", None) == KnowledgeVisibility.INTERNAL:
        return []
    normalized = normalize_identifier_value(identifier_value)
    if not normalized:
        return []
    if max_hits is None:
        try:
            max_hits = int(getattr(settings, "DATASET_KEY_INDEX_MAX_MATCHES_PER_UPLOAD", 8) or 8)
        except (TypeError, ValueError):
            max_hits = 8
    max_hits = max(1, min(50, int(max_hits)))

    upload_name = (upload.display_name or upload.filename or str(upload.id)).strip()
    hits: list[DatasetKeyIndexHit] = []
    for entry in _iter_upload_key_indexes(upload):
        storage_path = str(entry.get("storage_path") or "").strip()
        try:
            bits = int(entry.get("bits") or 0)
            hashes = int(entry.get("hashes") or 0)
        except (TypeError, ValueError):
            continue
        bloom = _load_bloom(storage_path=storage_path, bits=bits, hashes=hashes)
        if not bloom:
            continue
        if not bloom.maybe_contains(normalized):
            continue
        column = str(entry.get("column") or "").strip() or "key"
        identifier_key = str(entry.get("identifier_key") or "").strip() or None
        identifier_required = entry.get("identifier_required")
        if identifier_required is not None:
            identifier_required = bool(identifier_required)
        sheet_name = str(entry.get("sheet_name") or "").strip() or None
        try:
            sheet_index = int(entry.get("sheet_index")) if entry.get("sheet_index") is not None else None
        except (TypeError, ValueError):
            sheet_index = None
        hits.append(
            DatasetKeyIndexHit(
                upload_id=str(upload.id),
                upload_name=upload_name,
                sheet_name=sheet_name,
                sheet_index=sheet_index,
                column=column,
                identifier_key=identifier_key,
                identifier_required=identifier_required,
                source=str(entry.get("source") or "").strip() or None,
            )
        )
        if len(hits) >= max_hits:
            return hits
    return hits
