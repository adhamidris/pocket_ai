"""
HubSpot API wrapper for native HubSpot CRM integration.

Uses the HubSpot CRM v3 API.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

HUBSPOT_API_BASE = "https://api.hubapi.com"
DEFAULT_TIMEOUT_S = 15


class HubSpotApiError(RuntimeError):
    """Raised when a HubSpot API call fails."""


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _check_response(response: requests.Response, operation: str) -> dict[str, Any]:
    if not response.ok:
        detail = ""
        try:
            error = response.json()
            detail = str(error.get("message", ""))[:200]
        except Exception:
            detail = response.text[:200]
        raise HubSpotApiError(f"HubSpot {operation} failed ({response.status_code}): {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise HubSpotApiError(f"HubSpot {operation} returned invalid JSON.") from exc


def hubspot_search_contacts(
    access_token: str,
    *,
    query: str,
    max_results: int = 10,
) -> dict[str, Any]:
    """Search contacts in HubSpot CRM."""
    body = {
        "query": query,
        "limit": min(max(1, max_results), 100),
        "properties": [
            "email", "firstname", "lastname", "phone",
            "company", "jobtitle", "lifecyclestage",
            "createdate", "lastmodifieddate",
        ],
    }

    try:
        response = requests.post(
            f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/search",
            headers=_headers(access_token),
            json=body,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise HubSpotApiError(f"Failed to reach HubSpot API: {exc.__class__.__name__}") from exc

    data = _check_response(response, "search_contacts")
    results_raw = data.get("results", [])
    results = []
    for item in results_raw:
        props = item.get("properties", {})
        results.append({
            "id": item.get("id"),
            "email": props.get("email", ""),
            "first_name": props.get("firstname", ""),
            "last_name": props.get("lastname", ""),
            "phone": props.get("phone", ""),
            "company": props.get("company", ""),
            "job_title": props.get("jobtitle", ""),
            "lifecycle_stage": props.get("lifecyclestage", ""),
            "created_at": props.get("createdate", ""),
            "updated_at": props.get("lastmodifieddate", ""),
        })
    return {
        "results": results,
        "result_count": len(results),
        "total": data.get("total", 0),
    }


def hubspot_get_contact(
    access_token: str,
    contact_id: str,
) -> dict[str, Any]:
    """Get a specific HubSpot contact by ID."""
    params = {
        "properties": ",".join([
            "email", "firstname", "lastname", "phone",
            "company", "jobtitle", "lifecyclestage",
            "address", "city", "state", "zip", "country",
            "website", "createdate", "lastmodifieddate",
            "hs_lead_status", "hubspot_owner_id",
        ]),
    }

    try:
        response = requests.get(
            f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/{contact_id}",
            headers=_headers(access_token),
            params=params,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise HubSpotApiError(f"Failed to reach HubSpot API: {exc.__class__.__name__}") from exc

    data = _check_response(response, "get_contact")
    props = data.get("properties", {})
    return {
        "id": data.get("id"),
        "email": props.get("email", ""),
        "first_name": props.get("firstname", ""),
        "last_name": props.get("lastname", ""),
        "phone": props.get("phone", ""),
        "company": props.get("company", ""),
        "job_title": props.get("jobtitle", ""),
        "lifecycle_stage": props.get("lifecyclestage", ""),
        "lead_status": props.get("hs_lead_status", ""),
        "address": props.get("address", ""),
        "city": props.get("city", ""),
        "state": props.get("state", ""),
        "zip": props.get("zip", ""),
        "country": props.get("country", ""),
        "website": props.get("website", ""),
        "owner_id": props.get("hubspot_owner_id", ""),
        "created_at": props.get("createdate", ""),
        "updated_at": props.get("lastmodifieddate", ""),
    }


def hubspot_create_contact(
    access_token: str,
    *,
    email: str,
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    company: str = "",
    job_title: str = "",
    lifecycle_stage: str = "",
) -> dict[str, Any]:
    """Create a new contact in HubSpot CRM."""
    properties: dict[str, str] = {"email": email}
    if first_name:
        properties["firstname"] = first_name
    if last_name:
        properties["lastname"] = last_name
    if phone:
        properties["phone"] = phone
    if company:
        properties["company"] = company
    if job_title:
        properties["jobtitle"] = job_title
    if lifecycle_stage:
        properties["lifecyclestage"] = lifecycle_stage

    try:
        response = requests.post(
            f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts",
            headers=_headers(access_token),
            json={"properties": properties},
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise HubSpotApiError(f"Failed to reach HubSpot API: {exc.__class__.__name__}") from exc

    data = _check_response(response, "create_contact")
    return {
        "id": data.get("id"),
        "email": email,
        "created": True,
    }


def hubspot_search_deals(
    access_token: str,
    *,
    query: str,
    max_results: int = 10,
) -> dict[str, Any]:
    """Search deals in HubSpot CRM."""
    body = {
        "query": query,
        "limit": min(max(1, max_results), 100),
        "properties": [
            "dealname", "amount", "dealstage", "pipeline",
            "closedate", "createdate", "hs_lastmodifieddate",
            "hubspot_owner_id",
        ],
    }

    try:
        response = requests.post(
            f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/search",
            headers=_headers(access_token),
            json=body,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise HubSpotApiError(f"Failed to reach HubSpot API: {exc.__class__.__name__}") from exc

    data = _check_response(response, "search_deals")
    results_raw = data.get("results", [])
    results = []
    for item in results_raw:
        props = item.get("properties", {})
        results.append({
            "id": item.get("id"),
            "deal_name": props.get("dealname", ""),
            "amount": props.get("amount", ""),
            "deal_stage": props.get("dealstage", ""),
            "pipeline": props.get("pipeline", ""),
            "close_date": props.get("closedate", ""),
            "owner_id": props.get("hubspot_owner_id", ""),
            "created_at": props.get("createdate", ""),
            "updated_at": props.get("hs_lastmodifieddate", ""),
        })
    return {
        "results": results,
        "result_count": len(results),
        "total": data.get("total", 0),
    }
