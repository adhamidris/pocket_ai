from __future__ import annotations

import json
import logging
import mimetypes
import uuid
from http import HTTPStatus
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import get_language, gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.accounts.models import KnowledgeAuditAction, KnowledgeSourceType
from apps.api.shared import _iso, _resolve_business_profile
from apps.knowledge.documents import (
    CsvPreviewError,
    DocumentDetail,
    DocumentListItem,
    DocumentListValidationError as KnowledgeDocumentListValidationError,
    DocumentScrapeError,
    delete_document as delete_knowledge_document,
    get_document_detail as get_knowledge_document_detail,
    get_document_summary as get_knowledge_document_summary,
    list_documents as list_knowledge_documents,
    preview_csv_upload,
    scrape_document_source,
)
from apps.knowledge.models import KnowledgeAuditEvent, KnowledgeUpload

logger = logging.getLogger(__name__)


def _serve_document_file(file_detail, *, download: bool) -> FileResponse:
    media_root = Path(getattr(settings, "MEDIA_ROOT", ""))
    if not media_root:
        raise PermissionError("MEDIA_ROOT is not configured.")

    media_root = media_root.resolve()
    storage_path = Path(file_detail.storage_path)
    absolute = (media_root / storage_path).resolve()
    try:
        absolute.relative_to(media_root)
    except ValueError as exc:
        raise PermissionError("Invalid storage path.") from exc

    if not absolute.exists():
        raise FileNotFoundError(file_detail.storage_path)

    filename = file_detail.filename or absolute.name
    content_type = file_detail.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = FileResponse(
        absolute.open("rb"),
        as_attachment=download,
        filename=filename,
    )
    response["Content-Type"] = content_type
    return response


def _format_document_display_timestamp(value) -> str:
    if not value:
        return _("—")
    localized = timezone.localtime(value)
    language = (get_language() or "").lower()
    if language.startswith("ar"):
        return date_format(localized, "d/m/Y h:i A")
    return date_format(localized, "M j, Y g:i A")


def _serialize_document_summary(item: DocumentListItem) -> dict:
    return {
        "id": str(item.id),
        "name": item.name,
        "status": item.status,
        "statusLabel": item.status_label,
        "sourceType": item.source_type,
        "sourceLabel": item.source_label,
        "language": item.language,
        "category": item.category,
        "tokenCount": item.token_count,
        "sizeBytes": item.size_bytes,
        "isSensitive": item.is_sensitive,
        "lastIngestedAt": _iso(item.last_ingested_at),
        "lastSyncedAt": _iso(item.last_synced_at),
        "lastSyncedDisplay": _format_document_display_timestamp(item.last_synced_at),
        "updatedAt": _iso(item.updated_at),
        "updatedDisplay": _format_document_display_timestamp(item.updated_at),
        "integrationName": item.integration_name,
        "ingestionError": item.ingestion_error,
    }

def _serialize_document_detail(detail: DocumentDetail) -> dict:
    payload = {
        "summary": _serialize_document_summary(detail.summary),
        "summaryText": detail.summary_text,
        "createdByAgent": detail.created_by_agent,
    }
    ingestion_meta = detail.ingestion_metadata if isinstance(detail.ingestion_metadata, dict) else None
    if ingestion_meta is not None:
        payload["ingestionMetadata"] = ingestion_meta
    quality_report = detail.ingestion_metadata.get("quality_report") if isinstance(detail.ingestion_metadata, dict) else None
    if isinstance(quality_report, dict) and quality_report:
        payload["qualityReport"] = quality_report
    if detail.pages:
        payload["layoutPages"] = [
            {
                "pageNumber": page.page_number,
                "width": page.width,
                "height": page.height,
                "rotation": page.rotation,
                "textDensity": page.text_density,
                "hasOcrContent": page.has_ocr_content,
                "contentType": page.content_type,
                "metadata": page.metadata,
                "blocks": [
                    {
                        "blockType": block.block_type,
                        "orderIndex": block.order_index,
                        "text": block.text,
                        "bbox": block.bbox,
                        "sectionHeading": block.section_heading,
                        "headingPath": list(block.heading_path),
                        "detectedLanguage": block.detected_language,
                        "confidence": block.confidence,
                        "metadata": block.metadata,
                    }
                    for block in page.blocks
                ],
            }
            for page in detail.pages
        ]
    if detail.tables:
        payload["structuredTables"] = [
            {
                "orderIndex": table.order_index,
                "title": table.title,
                "sectionHeading": table.section_heading,
                "pageNumber": table.page_number,
                "columnSchema": list(table.column_schema),
                "rowCount": table.row_count,
                "bbox": table.bbox,
                "metadata": table.metadata,
                "rows": [
                    {
                        "rowIndex": row.row_index,
                        "pageNumber": row.page_number,
                        "bbox": row.bbox,
                        "rawText": row.raw_text,
                        "metadata": row.metadata,
                        "cells": [
                            {
                                "columnIndex": cell.column_index,
                                "columnKey": cell.column_key,
                                "rawText": cell.raw_text,
                                "normalizedValue": cell.normalized_value,
                                "bbox": cell.bbox,
                                "confidence": cell.confidence,
                                "metadata": cell.metadata,
                            }
                            for cell in row.cells
                        ],
                    }
                    for row in table.rows
                ],
            }
            for table in detail.tables
        ]
    if detail.issues:
        payload["issues"] = [
            {
                "code": issue.code,
                "severity": issue.severity,
                "description": issue.description,
                "pageNumber": issue.page_number,
                "tableOrderIndex": issue.table_order_index,
                "rowIndex": issue.row_index,
                "columnIndex": issue.column_index,
                "details": issue.details,
                "createdAt": _iso(issue.created_at),
            }
            for issue in detail.issues
        ]
    if detail.chunks:
        payload["chunks"] = [
            {
                "index": chunk.index,
                "content": chunk.content,
                "tokenCount": chunk.token_count,
                "metadata": chunk.metadata,
            }
            for chunk in detail.chunks
        ]
    return payload


@require_http_methods(["GET"])
def knowledge_documents_collection(request: HttpRequest) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    q_name = request.GET.get("q")
    status = request.GET.get("status")
    source_type = request.GET.get("source_type")
    limit = request.GET.get("limit") or 50
    offset = request.GET.get("offset") or 0

    try:
        result = list_knowledge_documents(
            business_profile=business,
            q_name=q_name,
            status=status,
            source_type=source_type,
            limit=limit,
            offset=offset,
        )
    except KnowledgeDocumentListValidationError as exc:
        logger.warning(
            "knowledge_documents_collection validation_error user=%s business=%s field=%s message=%s",
            getattr(request.user, "id", None),
            getattr(business, "id", None),
            exc.field,
            exc,
        )
        payload = {
            "error": "VALIDATION_ERROR",
            "message": str(exc),
        }
        if exc.field:
            payload["field"] = exc.field
        return JsonResponse(payload, status=HTTPStatus.BAD_REQUEST)

    logger.info(
        "knowledge_documents_collection user=%s business=%s total=%s limit=%s offset=%s filters=%s",
        getattr(request.user, "id", None),
        business.id,
        result.total,
        result.limit,
        result.offset,
        {
            "q": q_name,
            "status": status,
            "source_type": source_type,
        },
    )

    response = {
        "items": [_serialize_document_summary(item) for item in result.items],
        "total": result.total,
        "limit": result.limit,
        "offset": result.offset,
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["GET"])
def knowledge_document_status(request: HttpRequest, document_id: uuid.UUID) -> JsonResponse:
    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    try:
        summary = get_knowledge_document_summary(business_profile=business, document_id=document_id)
    except KnowledgeUpload.DoesNotExist:
        logger.warning(
            "knowledge_document_status not_found user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    return JsonResponse({"document": _serialize_document_summary(summary)}, status=HTTPStatus.OK)


@require_http_methods(["GET", "DELETE", "PATCH"])
def knowledge_document_detail(request: HttpRequest, document_id: uuid.UUID):
    payload: dict | None = None
    if request.method == "PATCH":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )
        business_id = request.GET.get("business_id") or (payload or {}).get("businessId")
    else:
        business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    if request.method == "PATCH":
        if not request.user.is_authenticated:
            return JsonResponse(
                {"error": "UNAUTHORIZED", "message": "Login required to update documents."},
                status=HTTPStatus.UNAUTHORIZED,
            )
        payload = payload or {}
        display_name_keys = ("display_name", "displayName", "name")
        display_name_provided = any(key in payload for key in display_name_keys)
        display_name = ""
        if display_name_provided:
            display_name = (
                str(payload.get("display_name") or payload.get("displayName") or payload.get("name") or "").strip()
            )
            if not display_name:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "Display name is required."},
                    status=HTTPStatus.BAD_REQUEST,
                )
            if len(display_name) > 255:
                return JsonResponse(
                    {"error": "VALIDATION_ERROR", "message": "Display name must be 255 characters or fewer."},
                    status=HTTPStatus.BAD_REQUEST,
                )

        if not display_name_provided:
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "No changes provided."},
                status=HTTPStatus.BAD_REQUEST,
            )

        upload = KnowledgeUpload.objects.filter(business_profile=business, id=document_id).first()
        if not upload:
            return JsonResponse(
                {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
                status=HTTPStatus.NOT_FOUND,
            )

        update_fields: list[str] = []
        if display_name_provided:
            upload.display_name = display_name[:255]
            update_fields.append("display_name")

        if update_fields:
            upload.save(update_fields=update_fields + ["updated_at"])
        logger.info(
            "knowledge_document_update user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"success": True, "document": {"id": str(upload.id), "name": upload.display_name}},
            status=HTTPStatus.OK,
        )

    if request.method == "DELETE":
        try:
            delete_knowledge_document(business_profile=business, document_id=document_id)
        except KnowledgeUpload.DoesNotExist:
            logger.warning(
                "knowledge_document_delete_not_found user=%s business=%s document=%s",
                getattr(request.user, "id", None),
                business.id,
                document_id,
            )
            return JsonResponse(
                {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
                status=HTTPStatus.NOT_FOUND,
            )

        logger.info(
            "knowledge_document_delete user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return HttpResponse(status=HTTPStatus.NO_CONTENT)

    try:
        detail = get_knowledge_document_detail(business_profile=business, document_id=document_id)
    except KnowledgeUpload.DoesNotExist:
        logger.warning(
            "knowledge_document_detail not_found user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "DOCUMENT_NOT_FOUND", "message": "Document not found."},
            status=HTTPStatus.NOT_FOUND,
        )

    logger.info(
        "knowledge_document_detail user=%s business=%s document=%s status=%s source=%s",
        getattr(request.user, "id", None),
        business.id,
        document_id,
        detail.summary.status,
        detail.summary.source_type,
    )

    return JsonResponse({"document": _serialize_document_detail(detail)}, status=HTTPStatus.OK)


@require_http_methods(["GET"])
def knowledge_document_download(request: HttpRequest, document_id: uuid.UUID):
    if not request.user.is_authenticated:
        logger.warning("knowledge_document_download unauthorized document=%s", document_id)
        return JsonResponse(
            {"error": "UNAUTHORIZED", "message": "Login required to download documents."},
            status=HTTPStatus.UNAUTHORIZED,
        )

    business_id = request.GET.get("business_id")
    business, error = _resolve_business_profile(request, business_id)
    if error:
        return error
    assert business is not None

    owns_business = request.user.is_staff or request.user.business_profiles.filter(id=business.id).exists()
    if not owns_business:
        logger.warning(
            "knowledge_document_download forbidden user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "FORBIDDEN", "message": "You do not have access to this document."},
            status=HTTPStatus.FORBIDDEN,
        )

    upload = (
        KnowledgeUpload.objects.filter(
            business_profile=business,
            id=document_id,
            source_type=KnowledgeSourceType.FILE,
        )
        .select_related("file_detail")
        .first()
    )
    if upload is None or not upload.file_detail:
        logger.warning(
            "knowledge_document_download missing_file user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "DOCUMENT_NOT_FOUND", "message": "File not found for download."},
            status=HTTPStatus.NOT_FOUND,
        )

    download = request.GET.get("download") == "1"
    try:
        response = _serve_document_file(upload.file_detail, download=download)
    except PermissionError:
        logger.error(
            "knowledge_document_download invalid_path user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "FILE_INVALID", "message": "File path is invalid."},
            status=HTTPStatus.BAD_REQUEST,
        )
    except FileNotFoundError:
        logger.error(
            "knowledge_document_download file_missing user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
        return JsonResponse(
            {"error": "FILE_MISSING", "message": "Original file is no longer available."},
            status=HTTPStatus.GONE,
        )

    logger.info(
        "knowledge_document_download success user=%s business=%s document=%s as_attachment=%s",
        getattr(request.user, "id", None),
        business.id,
        document_id,
        download,
    )
    try:
        KnowledgeAuditEvent.objects.create(
            business_profile=business,
            upload=upload,
            upload_id_snapshot=upload.id,
            actor_user=request.user,
            action=KnowledgeAuditAction.EXPORTED,
            description="Knowledge document downloaded.",
            metadata={
                "endpoint": "knowledge_document_download",
                "as_attachment": bool(download),
            },
        )
    except Exception:
        logger.exception(
            "knowledge.audit_export_failed user=%s business=%s document=%s",
            getattr(request.user, "id", None),
            business.id,
            document_id,
        )
    return response


@require_http_methods(["POST"])
def knowledge_document_scrape(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "INVALID_JSON", "message": "Request body must be valid JSON."},
            status=HTTPStatus.BAD_REQUEST,
        )

    url = str(payload.get("url") or "").strip()
    if not url:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Provide a URL to scrape.", "field": "url"},
            status=HTTPStatus.BAD_REQUEST,
        )

    timeout = payload.get("timeout")
    max_bytes = payload.get("maxBytes")

    timeout_value = 10.0
    if timeout is not None:
        try:
            timeout_value = max(1.0, min(float(timeout), 30.0))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "timeout must be numeric.", "field": "timeout"},
                status=HTTPStatus.BAD_REQUEST,
            )

    max_bytes_value = 2_000_000
    if max_bytes is not None:
        try:
            max_bytes_value = int(max_bytes)
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "maxBytes must be numeric.", "field": "maxBytes"},
                status=HTTPStatus.BAD_REQUEST,
            )
        max_bytes_value = max(100_000, min(max_bytes_value, 5_000_000))

    try:
        scraped = scrape_document_source(url=url, timeout=timeout_value, max_bytes=max_bytes_value)
    except DocumentScrapeError as exc:
        return JsonResponse(
            {"error": "SCRAPE_FAILED", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )

    response = {
        "scraped": {
            "url": scraped.url,
            "finalUrl": scraped.final_url,
            "statusCode": scraped.status_code,
            "contentType": scraped.content_type,
            "elapsedMs": scraped.elapsed_ms,
            "contentLength": scraped.content_length,
            "truncated": scraped.truncated,
            "preview": scraped.preview,
            "wordCount": scraped.word_count,
            "text": scraped.text,
        }
    }
    return JsonResponse(response, status=HTTPStatus.OK)


@require_http_methods(["POST"])
def knowledge_document_preview_csv(request: HttpRequest) -> JsonResponse:
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Upload a CSV file to preview.", "field": "file"},
            status=HTTPStatus.BAD_REQUEST,
        )

    max_rows = request.POST.get("max_rows")
    max_rows_value = 50
    if max_rows is not None:
        try:
            max_rows_value = max(1, min(int(max_rows), 200))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "max_rows must be numeric.", "field": "max_rows"},
                status=HTTPStatus.BAD_REQUEST,
            )

    try:
        preview = preview_csv_upload(upload, max_rows=max_rows_value)
    except CsvPreviewError as exc:
        return JsonResponse(
            {"error": "CSV_PREVIEW_FAILED", "message": str(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )

    response = {
        "preview": {
            "columns": list(preview.columns),
            "rows": [list(row) for row in preview.rows],
            "rowCount": preview.row_count,
            "truncated": preview.truncated,
            "dialect": preview.dialect,
        }
    }
    return JsonResponse(response, status=HTTPStatus.OK)
