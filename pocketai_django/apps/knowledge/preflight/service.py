from __future__ import annotations

import logging
from typing import Any, Mapping
from urllib.parse import urlparse

from django.utils import timezone

from apps.accounts.models import KnowledgeSourceType
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadFile,
    KnowledgeUploadText,
    KnowledgeUploadUrl,
)
from apps.knowledge.preflight.file_inspection import (
    _detect_format,
    _preflight_csv_like,
    _preflight_excel_like,
    _preflight_json_like,
    _resolve_media_path,
    _safe_pdf_page_count,
    load_workbook,
    xlrd,
)
from apps.knowledge.preflight.table_limits import (
    _table_limits_snapshot,
    _table_size_warnings,
)

logger = logging.getLogger(__name__)

PREFLIGHT_VERSION = 1


def ensure_upload_preflight(
    upload: KnowledgeUpload,
    *,
    trigger: str | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """
    Lightweight, bounded scan to summarize what we *can* do with an upload.

    Stores results under `upload.ingestion_metadata["preflight"]` so the dashboard
    and API can show tenants limitations/next steps before (or even if) ingestion
    fails.
    """

    if not upload:
        return None

    ingestion_metadata = dict(upload.ingestion_metadata or {})
    existing = ingestion_metadata.get("preflight")
    if not force and isinstance(existing, Mapping) and existing.get("version") == PREFLIGHT_VERSION:
        return dict(existing)

    performed_at = timezone.now().isoformat()
    base: dict[str, Any] = {
        "version": PREFLIGHT_VERSION,
        "performed_at": performed_at,
        "trigger": trigger or "",
        "source_type": upload.source_type,
        "status": "ok",
        "format": None,
        "suggested_kind": None,
        "warnings": [],
        "recommendations": [],
        "limits": {},
        "metrics": {},
    }

    try:
        if upload.source_type in {KnowledgeSourceType.FILE, KnowledgeSourceType.INTEGRATION}:
            try:
                file_detail = upload.file_detail
            except KnowledgeUploadFile.DoesNotExist:
                file_detail = None
            if not file_detail:
                base["status"] = "error"
                base["warnings"].append("Missing file metadata; ingestion cannot run.")
            else:
                base.update(_preflight_file(upload, file_detail))
        elif upload.source_type == KnowledgeSourceType.LINK:
            try:
                url_detail = upload.url_detail
            except KnowledgeUploadUrl.DoesNotExist:
                url_detail = None
            if not url_detail:
                base["status"] = "error"
                base["warnings"].append("Missing URL metadata; ingestion cannot run.")
            else:
                base.update(_preflight_url(upload, url_detail))
        elif upload.source_type == KnowledgeSourceType.TEXT:
            try:
                text_detail = upload.text_detail
            except KnowledgeUploadText.DoesNotExist:
                text_detail = None
            if not text_detail:
                base["status"] = "error"
                base["warnings"].append("Missing text metadata; ingestion cannot run.")
            else:
                base.update(_preflight_text(upload, text_detail))
        else:
            base["status"] = "error"
            base["warnings"].append(f"Unsupported source type: {upload.source_type}")
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("knowledge.preflight.failed upload=%s", getattr(upload, "id", None))
        base["status"] = "error"
        base["warnings"].append(f"Preflight failed: {exc}")

    ingestion_metadata["preflight"] = base
    upload.ingestion_metadata = ingestion_metadata
    upload.save(update_fields=["ingestion_metadata", "updated_at"])
    logger.info("knowledge.preflight upload=%s status=%s format=%s", upload.id, base.get("status"), base.get("format"))
    return base


def _preflight_url(upload: KnowledgeUpload, url_detail: KnowledgeUploadUrl) -> dict[str, Any]:
    parsed = urlparse(url_detail.url or "")
    host = parsed.netloc or parsed.path
    warnings: list[str] = []
    recommendations: list[str] = []

    if not url_detail.url:
        warnings.append("URL is empty; ingestion cannot fetch content.")
    if not host:
        warnings.append("URL host could not be determined; ingestion may fail.")
    recommendations.append("If the URL requires login, export/upload the file instead.")

    return {
        "format": "url",
        "suggested_kind": "document",
        "metrics": {
            "url": url_detail.url,
            "host": host,
        },
        "warnings": warnings,
        "recommendations": recommendations,
    }


def _preflight_text(upload: KnowledgeUpload, text_detail: KnowledgeUploadText) -> dict[str, Any]:
    content = text_detail.content or ""
    chars = len(content)
    preview = content.strip().splitlines()[0][:120] if content.strip() else ""
    warnings: list[str] = []
    if chars > 200_000:
        warnings.append("Very large text entry; consider splitting into multiple documents for better retrieval.")
    return {
        "format": "text",
        "suggested_kind": "document",
        "metrics": {
            "characters": chars,
            "preview": preview,
        },
        "warnings": warnings,
        "recommendations": [],
    }


def _preflight_file(upload: KnowledgeUpload, file_detail: KnowledgeUploadFile) -> dict[str, Any]:
    filename = file_detail.filename or ""
    content_type = file_detail.content_type or ""
    fmt = _detect_format(filename, content_type)
    warnings: list[str] = []
    recommendations: list[str] = []
    status = "ok"

    absolute_path = None
    try:
        absolute_path = _resolve_media_path(file_detail.storage_path)
    except Exception as exc:
        status = "error"
        warnings.append(f"Storage path is invalid: {exc}")

    size_bytes = int(file_detail.size_bytes or getattr(upload, "size_bytes", 0) or 0)
    if absolute_path and absolute_path.exists():
        try:
            size_bytes = int(absolute_path.stat().st_size)
        except OSError:
            pass
    elif absolute_path:
        status = "error"
        warnings.append("File is missing from storage; ingestion cannot read it.")

    metrics: dict[str, Any] = {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "storage_path": file_detail.storage_path,
    }

    if fmt in {"csv", "tsv"}:
        metrics.update(_preflight_csv_like(absolute_path, fmt, warnings))
        limits = _table_limits_snapshot(upload)
        metrics.update(_table_size_warnings(metrics, limits, warnings, recommendations))
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "dataset",
            "limits": {"table_limits": limits},
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": recommendations,
        }

    if fmt in {"xlsx", "xls"}:
        if fmt == "xlsx" and load_workbook is None:
            status = "error"
        if fmt == "xls" and xlrd is None:
            status = "error"
        metrics.update(_preflight_excel_like(absolute_path, fmt, warnings))
        limits = _table_limits_snapshot(upload)
        metrics.update(_table_size_warnings(metrics, limits, warnings, recommendations))
        recommendations.append("For very large sheets, prefer CSV exports or split by time/region/product.")
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "dataset",
            "limits": {"table_limits": limits},
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": recommendations,
        }

    if fmt == "pdf":
        page_count = _safe_pdf_page_count(absolute_path)
        if page_count is not None:
            metrics["page_count"] = page_count
            if page_count > 250:
                warnings.append("Large PDF (250+ pages); ingestion can be slow and retrieval may be noisier.")
        if size_bytes > 25 * 1024 * 1024:
            warnings.append("Large file size (25MB+); ingestion can be slow.")
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "document",
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": [],
        }

    if fmt in {"docx", "txt", "md", "rtf"} or (fmt and fmt.startswith("txt")):
        if size_bytes > 10 * 1024 * 1024:
            warnings.append("Large text document (10MB+); consider splitting for better retrieval.")
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "document",
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": [],
        }

    if fmt == "json":
        metrics.update(_preflight_json_like(absolute_path, warnings))
        if metrics.get("json_detected") == "jsonl":
            limits = _table_limits_snapshot(upload)
            metrics.update(_table_size_warnings(metrics, limits, warnings, recommendations))
        return {
            "status": status,
            "format": fmt,
            "suggested_kind": "dataset",
            "metrics": metrics,
            "warnings": warnings,
            "recommendations": recommendations,
        }

    warnings.append("Unknown file type; ingestion may fail or produce poor retrieval.")
    return {
        "status": status,
        "format": fmt or "",
        "suggested_kind": "document",
        "metrics": metrics,
        "warnings": warnings,
        "recommendations": recommendations,
    }
