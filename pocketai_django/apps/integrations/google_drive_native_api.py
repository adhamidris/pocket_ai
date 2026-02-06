"""
Google Drive API wrapper for native drive integration (tool access).

Named _native_ to distinguish from the existing google_drive.py used for
knowledge sync. Uses the Google Drive API v3.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DEFAULT_TIMEOUT_S = 15

# MIME types we can reasonably extract text from
_TEXT_EXPORTABLE_MIMES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_TEXT_DOWNLOADABLE_MIMES = {
    "text/plain",
    "text/csv",
    "text/html",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
}


class DriveApiError(RuntimeError):
    """Raised when a Google Drive API call fails."""


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _check_response(response: requests.Response, operation: str) -> dict[str, Any]:
    if not response.ok:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")[:200]
        except Exception:
            detail = response.text[:200]
        raise DriveApiError(f"Google Drive {operation} failed ({response.status_code}): {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise DriveApiError(f"Google Drive {operation} returned invalid JSON.") from exc


def drive_search_files(
    access_token: str,
    *,
    query: str,
    max_results: int = 10,
    mime_type_filter: str | None = None,
) -> dict[str, Any]:
    """Search files in Google Drive by name/content."""
    q_parts = [f"fullText contains '{query.replace(chr(39), chr(39)+chr(39))}'"]
    if mime_type_filter:
        q_parts.append(f"mimeType = '{mime_type_filter}'")
    q_parts.append("trashed = false")

    params = {
        "q": " and ".join(q_parts),
        "pageSize": min(max(1, max_results), 50),
        "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink,owners),nextPageToken",
        "orderBy": "modifiedTime desc",
    }

    try:
        response = requests.get(
            f"{DRIVE_API_BASE}/files",
            headers=_headers(access_token),
            params=params,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise DriveApiError(f"Failed to reach Google Drive API: {exc.__class__.__name__}") from exc

    data = _check_response(response, "search_files")
    files = data.get("files", [])
    results = []
    for f in files:
        owners = f.get("owners", [])
        results.append({
            "id": f.get("id"),
            "name": f.get("name", ""),
            "mime_type": f.get("mimeType", ""),
            "modified_time": f.get("modifiedTime", ""),
            "size": f.get("size"),
            "web_view_link": f.get("webViewLink", ""),
            "owner": owners[0].get("emailAddress", "") if owners else "",
        })
    return {
        "results": results,
        "result_count": len(results),
        "next_page_token": data.get("nextPageToken"),
    }


def drive_get_file_content(
    access_token: str,
    file_id: str,
    *,
    max_chars: int = 8000,
) -> dict[str, Any]:
    """Get file content (text-based files) or metadata for binary files."""
    # First get file metadata
    try:
        meta_resp = requests.get(
            f"{DRIVE_API_BASE}/files/{file_id}",
            headers=_headers(access_token),
            params={"fields": "id,name,mimeType,modifiedTime,size,webViewLink"},
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise DriveApiError(f"Failed to reach Google Drive API: {exc.__class__.__name__}") from exc

    meta = _check_response(meta_resp, "get_file_metadata")
    mime_type = str(meta.get("mimeType") or "")

    result: dict[str, Any] = {
        "id": meta.get("id"),
        "name": meta.get("name", ""),
        "mime_type": mime_type,
        "modified_time": meta.get("modifiedTime", ""),
        "size": meta.get("size"),
        "web_view_link": meta.get("webViewLink", ""),
    }

    # Try to export Google Workspace files
    export_mime = _TEXT_EXPORTABLE_MIMES.get(mime_type)
    if export_mime:
        try:
            export_resp = requests.get(
                f"{DRIVE_API_BASE}/files/{file_id}/export",
                headers=_headers(access_token),
                params={"mimeType": export_mime},
                timeout=DEFAULT_TIMEOUT_S,
            )
            if export_resp.ok:
                result["content"] = export_resp.text[:max_chars]
                result["content_truncated"] = len(export_resp.text) > max_chars
                return result
        except requests.RequestException:
            pass

    # Try to download text-based files
    if mime_type in _TEXT_DOWNLOADABLE_MIMES:
        try:
            dl_resp = requests.get(
                f"{DRIVE_API_BASE}/files/{file_id}",
                headers=_headers(access_token),
                params={"alt": "media"},
                timeout=DEFAULT_TIMEOUT_S,
            )
            if dl_resp.ok:
                result["content"] = dl_resp.text[:max_chars]
                result["content_truncated"] = len(dl_resp.text) > max_chars
                return result
        except requests.RequestException:
            pass

    result["content"] = None
    result["hint"] = "File content cannot be extracted (binary or unsupported format). Use the web_view_link to view."
    return result


def drive_list_files(
    access_token: str,
    *,
    folder_id: str | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """List files in a Google Drive folder (or root)."""
    q_parts = ["trashed = false"]
    if folder_id:
        q_parts.append(f"'{folder_id}' in parents")

    params = {
        "q": " and ".join(q_parts),
        "pageSize": min(max(1, max_results), 100),
        "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink),nextPageToken",
        "orderBy": "folder,name",
    }

    try:
        response = requests.get(
            f"{DRIVE_API_BASE}/files",
            headers=_headers(access_token),
            params=params,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise DriveApiError(f"Failed to reach Google Drive API: {exc.__class__.__name__}") from exc

    data = _check_response(response, "list_files")
    files = data.get("files", [])
    results = []
    for f in files:
        results.append({
            "id": f.get("id"),
            "name": f.get("name", ""),
            "mime_type": f.get("mimeType", ""),
            "modified_time": f.get("modifiedTime", ""),
            "size": f.get("size"),
            "web_view_link": f.get("webViewLink", ""),
            "is_folder": f.get("mimeType") == "application/vnd.google-apps.folder",
        })
    return {
        "results": results,
        "result_count": len(results),
        "next_page_token": data.get("nextPageToken"),
    }
