"""
Google Calendar API wrapper for native calendar integration.

Uses the Google Calendar API v3.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
DEFAULT_TIMEOUT_S = 15


class CalendarApiError(RuntimeError):
    """Raised when a Google Calendar API call fails."""


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _check_response(response: requests.Response, operation: str) -> dict[str, Any]:
    if not response.ok:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")[:200]
        except Exception:
            detail = response.text[:200]
        raise CalendarApiError(f"Google Calendar {operation} failed ({response.status_code}): {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise CalendarApiError(f"Google Calendar {operation} returned invalid JSON.") from exc


def calendar_list_events(
    access_token: str,
    *,
    calendar_id: str = "primary",
    time_min: str | None = None,
    time_max: str | None = None,
    query: str | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """List events from a Google Calendar."""
    params: dict[str, str | int] = {
        "maxResults": min(max(1, max_results), 50),
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    if query:
        params["q"] = query

    try:
        response = requests.get(
            f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events",
            headers=_headers(access_token),
            params=params,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise CalendarApiError(f"Failed to reach Google Calendar API: {exc.__class__.__name__}") from exc

    data = _check_response(response, "list_events")
    items = data.get("items", [])
    results = []
    for item in items:
        start = item.get("start", {})
        end = item.get("end", {})
        results.append({
            "id": item.get("id"),
            "summary": item.get("summary", ""),
            "description": (item.get("description") or "")[:500],
            "start": start.get("dateTime") or start.get("date", ""),
            "end": end.get("dateTime") or end.get("date", ""),
            "location": item.get("location", ""),
            "status": item.get("status", ""),
            "html_link": item.get("htmlLink", ""),
            "attendees": [
                {"email": a.get("email", ""), "response_status": a.get("responseStatus", "")}
                for a in (item.get("attendees") or [])[:20]
            ],
            "organizer": item.get("organizer", {}).get("email", ""),
        })
    return {
        "results": results,
        "result_count": len(results),
        "next_page_token": data.get("nextPageToken"),
    }


def calendar_get_event(
    access_token: str,
    event_id: str,
    *,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """Get a specific calendar event by ID."""
    try:
        response = requests.get(
            f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
            headers=_headers(access_token),
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise CalendarApiError(f"Failed to reach Google Calendar API: {exc.__class__.__name__}") from exc

    item = _check_response(response, "get_event")
    start = item.get("start", {})
    end = item.get("end", {})
    return {
        "id": item.get("id"),
        "summary": item.get("summary", ""),
        "description": (item.get("description") or "")[:2000],
        "start": start.get("dateTime") or start.get("date", ""),
        "end": end.get("dateTime") or end.get("date", ""),
        "location": item.get("location", ""),
        "status": item.get("status", ""),
        "html_link": item.get("htmlLink", ""),
        "attendees": [
            {"email": a.get("email", ""), "response_status": a.get("responseStatus", "")}
            for a in (item.get("attendees") or [])[:50]
        ],
        "organizer": item.get("organizer", {}).get("email", ""),
        "creator": item.get("creator", {}).get("email", ""),
        "created": item.get("created", ""),
        "updated": item.get("updated", ""),
        "recurrence": item.get("recurrence"),
        "conference_data": _summarize_conference(item.get("conferenceData")),
    }


def _summarize_conference(data: dict | None) -> dict[str, str] | None:
    if not data or not isinstance(data, dict):
        return None
    entry_points = data.get("entryPoints", [])
    if not entry_points:
        return None
    primary = entry_points[0] if entry_points else {}
    return {
        "type": primary.get("entryPointType", ""),
        "uri": primary.get("uri", ""),
    }


def calendar_create_event(
    access_token: str,
    *,
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
    location: str = "",
) -> dict[str, Any]:
    """Create a new calendar event."""
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees[:50]]

    try:
        response = requests.post(
            f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events",
            headers=_headers(access_token),
            json=body,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise CalendarApiError(f"Failed to reach Google Calendar API: {exc.__class__.__name__}") from exc

    item = _check_response(response, "create_event")
    return {
        "id": item.get("id"),
        "summary": item.get("summary", ""),
        "html_link": item.get("htmlLink", ""),
        "status": item.get("status", ""),
    }


def calendar_update_event(
    access_token: str,
    event_id: str,
    *,
    updates: dict[str, Any],
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """Update an existing calendar event (PATCH)."""
    body: dict[str, Any] = {}
    if "summary" in updates:
        body["summary"] = updates["summary"]
    if "description" in updates:
        body["description"] = updates["description"]
    if "start_time" in updates:
        body["start"] = {"dateTime": updates["start_time"]}
    if "end_time" in updates:
        body["end"] = {"dateTime": updates["end_time"]}
    if "location" in updates:
        body["location"] = updates["location"]

    if not body:
        raise CalendarApiError("No valid update fields provided.")

    try:
        response = requests.patch(
            f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
            headers=_headers(access_token),
            json=body,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise CalendarApiError(f"Failed to reach Google Calendar API: {exc.__class__.__name__}") from exc

    item = _check_response(response, "update_event")
    return {
        "id": item.get("id"),
        "summary": item.get("summary", ""),
        "html_link": item.get("htmlLink", ""),
        "status": item.get("status", ""),
    }
