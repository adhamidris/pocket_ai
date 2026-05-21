"""
Slack API wrapper for native Slack integration.

Uses the Slack Web API.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"
DEFAULT_TIMEOUT_S = 15


class SlackApiError(RuntimeError):
    """Raised when a Slack API call fails."""


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _check_response(data: dict[str, Any], operation: str) -> dict[str, Any]:
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        raise SlackApiError(f"Slack {operation} failed: {error}")
    return data


def _call_slack(access_token: str, method: str, *, params: dict | None = None, json_body: dict | None = None) -> dict[str, Any]:
    """Make a Slack API call."""
    url = f"{SLACK_API_BASE}/{method}"
    try:
        if json_body is not None:
            response = requests.post(url, headers=_headers(access_token), json=json_body, timeout=DEFAULT_TIMEOUT_S)
        elif params is not None:
            response = requests.get(url, headers=_headers(access_token), params=params, timeout=DEFAULT_TIMEOUT_S)
        else:
            response = requests.get(url, headers=_headers(access_token), timeout=DEFAULT_TIMEOUT_S)
    except requests.RequestException as exc:
        raise SlackApiError(f"Failed to reach Slack API: {exc.__class__.__name__}") from exc

    if not response.ok:
        raise SlackApiError(f"Slack {method} HTTP error ({response.status_code})")

    try:
        data = response.json()
    except ValueError as exc:
        raise SlackApiError(f"Slack {method} returned invalid JSON.") from exc

    return _check_response(data, method)


def slack_list_channels(
    access_token: str,
    *,
    max_results: int = 20,
    types: str = "public_channel,private_channel",
) -> dict[str, Any]:
    """List Slack channels the user can access."""
    data = _call_slack(access_token, "conversations.list", params={
        "limit": min(max(1, max_results), 100),
        "types": types,
        "exclude_archived": "true",
    })

    channels = data.get("channels", [])
    results = []
    for ch in channels:
        results.append({
            "id": ch.get("id"),
            "name": ch.get("name", ""),
            "is_private": ch.get("is_private", False),
            "topic": (ch.get("topic") or {}).get("value", ""),
            "purpose": (ch.get("purpose") or {}).get("value", ""),
            "num_members": ch.get("num_members", 0),
        })
    return {
        "results": results,
        "result_count": len(results),
    }


def slack_read_channel(
    access_token: str,
    channel_id: str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Read recent messages from a Slack channel."""
    data = _call_slack(access_token, "conversations.history", params={
        "channel": channel_id,
        "limit": min(max(1, limit), 100),
    })

    messages = data.get("messages", [])
    results = []
    for msg in messages:
        results.append({
            "ts": msg.get("ts", ""),
            "user": msg.get("user", ""),
            "text": (msg.get("text") or "")[:2000],
            "type": msg.get("type", ""),
            "thread_ts": msg.get("thread_ts"),
            "reply_count": msg.get("reply_count", 0),
        })
    return {
        "results": results,
        "result_count": len(results),
        "has_more": data.get("has_more", False),
    }


def slack_send_message(
    access_token: str,
    *,
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Send a message to a Slack channel."""
    body: dict[str, Any] = {
        "channel": channel_id,
        "text": text,
    }
    if thread_ts:
        body["thread_ts"] = thread_ts

    data = _call_slack(access_token, "chat.postMessage", json_body=body)
    msg = data.get("message", {})
    return {
        "ok": True,
        "channel": data.get("channel", channel_id),
        "ts": msg.get("ts", data.get("ts", "")),
        "text": (msg.get("text") or "")[:500],
    }


def slack_search_messages(
    access_token: str,
    *,
    query: str,
    max_results: int = 10,
) -> dict[str, Any]:
    """Search messages across Slack workspace."""
    data = _call_slack(access_token, "search.messages", params={
        "query": query,
        "count": min(max(1, max_results), 50),
        "sort": "timestamp",
        "sort_dir": "desc",
    })

    matches_data = data.get("messages", {})
    matches = matches_data.get("matches", [])
    results = []
    for match in matches:
        results.append({
            "text": (match.get("text") or "")[:1000],
            "ts": match.get("ts", ""),
            "user": match.get("user", "") or match.get("username", ""),
            "channel_id": (match.get("channel") or {}).get("id", ""),
            "channel_name": (match.get("channel") or {}).get("name", ""),
            "permalink": match.get("permalink", ""),
        })
    return {
        "results": results,
        "result_count": len(results),
        "total_matches": matches_data.get("total", 0),
    }
