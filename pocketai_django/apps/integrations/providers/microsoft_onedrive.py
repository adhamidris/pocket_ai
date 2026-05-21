"""
OneDrive API wrapper via Microsoft Graph for native integration.

Uses the Microsoft Graph v1.0 Drive API.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

GRAPH_DRIVE_BASE = "https://graph.microsoft.com/v1.0/me/drive"
DEFAULT_TIMEOUT_S = 15

# MIME types we can reasonably extract text from via download
_TEXT_DOWNLOADABLE_MIMES = {
    "text/plain",
    "text/csv",
    "text/html",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
}


class OneDriveApiError(RuntimeError):
    """Raised when a OneDrive/Graph API call fails."""


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _check_response(response: requests.Response, operation: str) -> dict[str, Any]:
    if not response.ok:
        detail = ""
        try:
            error = response.json().get("error", {})
            detail = error.get("message", "")[:200]
        except Exception:
            detail = response.text[:200]
        raise OneDriveApiError(f"OneDrive {operation} failed ({response.status_code}): {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise OneDriveApiError(f"OneDrive {operation} returned invalid JSON.") from exc


def onedrive_search_files(
    access_token: str,
    *,
    query: str,
    max_results: int = 10,
) -> dict[str, Any]:
    """Search files in OneDrive."""
    params = {
        "$top": min(max(1, max_results), 50),
    }

    try:
        response = requests.get(
            f"{GRAPH_DRIVE_BASE}/root/search(q='{query}')",
            headers=_headers(access_token),
            params=params,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise OneDriveApiError(f"Failed to reach Microsoft Graph: {exc.__class__.__name__}") from exc

    data = _check_response(response, "search_files")
    items = data.get("value", [])
    results = []
    for item in items:
        results.append(_format_item(item))
    return {
        "results": results,
        "result_count": len(results),
    }


def onedrive_get_file_content(
    access_token: str,
    item_id: str,
    *,
    max_chars: int = 8000,
) -> dict[str, Any]:
    """Get file content (text-based files) or metadata for binary files."""
    # Get item metadata
    try:
        meta_resp = requests.get(
            f"{GRAPH_DRIVE_BASE}/items/{item_id}",
            headers=_headers(access_token),
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise OneDriveApiError(f"Failed to reach Microsoft Graph: {exc.__class__.__name__}") from exc

    meta = _check_response(meta_resp, "get_file_metadata")
    result = _format_item(meta)

    mime_type = str(meta.get("file", {}).get("mimeType") or "")
    download_url = str(meta.get("@microsoft.graph.downloadUrl") or "").strip()

    # Try to get text content for text-based files
    if download_url and mime_type in _TEXT_DOWNLOADABLE_MIMES:
        try:
            dl_resp = requests.get(download_url, timeout=DEFAULT_TIMEOUT_S)
            if dl_resp.ok:
                result["content"] = dl_resp.text[:max_chars]
                result["content_truncated"] = len(dl_resp.text) > max_chars
                return result
        except requests.RequestException:
            pass

    # Try the content endpoint as fallback
    if mime_type in _TEXT_DOWNLOADABLE_MIMES:
        try:
            content_resp = requests.get(
                f"{GRAPH_DRIVE_BASE}/items/{item_id}/content",
                headers=_headers(access_token),
                timeout=DEFAULT_TIMEOUT_S,
                allow_redirects=True,
            )
            if content_resp.ok:
                result["content"] = content_resp.text[:max_chars]
                result["content_truncated"] = len(content_resp.text) > max_chars
                return result
        except requests.RequestException:
            pass

    result["content"] = None
    result["hint"] = "File content cannot be extracted (binary or unsupported format). Use the web_url to view."
    return result


def onedrive_list_files(
    access_token: str,
    *,
    folder_id: str | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """List files in a OneDrive folder (or root)."""
    if folder_id:
        url = f"{GRAPH_DRIVE_BASE}/items/{folder_id}/children"
    else:
        url = f"{GRAPH_DRIVE_BASE}/root/children"

    params = {
        "$top": min(max(1, max_results), 100),
        "$orderby": "name",
    }

    try:
        response = requests.get(
            url,
            headers=_headers(access_token),
            params=params,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise OneDriveApiError(f"Failed to reach Microsoft Graph: {exc.__class__.__name__}") from exc

    data = _check_response(response, "list_files")
    items = data.get("value", [])
    results = []
    for item in items:
        results.append(_format_item(item))
    return {
        "results": results,
        "result_count": len(results),
    }


def _format_item(item: dict[str, Any]) -> dict[str, Any]:
    """Format a Graph DriveItem into a consistent dict."""
    return {
        "id": item.get("id"),
        "name": item.get("name", ""),
        "mime_type": (item.get("file") or {}).get("mimeType", ""),
        "modified_time": item.get("lastModifiedDateTime", ""),
        "size": item.get("size"),
        "web_url": item.get("webUrl", ""),
        "is_folder": "folder" in item,
        "created_time": item.get("createdDateTime", ""),
    }
