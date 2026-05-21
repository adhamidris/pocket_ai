from __future__ import annotations

from typing import Mapping

from apps.accounts.models import IntegrationType
from apps.conversations.models import Conversation
from apps.integrations.providers.hubspot import (
    HubSpotApiError,
    hubspot_create_contact,
    hubspot_get_contact,
    hubspot_search_contacts,
    hubspot_search_deals,
)

from ...types import ToolExecutionContext
from .shared import (
    _coerce_str,
    _get_integration_access_token,
    _integration_error,
    _resolve_integration_account_for_tool,
)


def _hubspot_search_contacts_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _integration_error("hubspot_search_contacts", error_code="missing_query", hint="query is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.HUBSPOT, tool="hubspot_search_contacts",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "hubspot_search_contacts")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = hubspot_search_contacts(access_token, query=query, max_results=max_results)
    except HubSpotApiError as exc:
        return _integration_error("hubspot_search_contacts", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "hubspot_search_contacts", "status": "ok", "integration_account_id": str(account.id), **payload}


def _hubspot_get_contact_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    contact_id = _coerce_str(arguments.get("contact_id")).strip()
    if not contact_id:
        return _integration_error("hubspot_get_contact", error_code="missing_contact_id", hint="contact_id is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.HUBSPOT, tool="hubspot_get_contact",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "hubspot_get_contact")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        payload = hubspot_get_contact(access_token, contact_id)
    except HubSpotApiError as exc:
        return _integration_error("hubspot_get_contact", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "hubspot_get_contact", "status": "ok", "integration_account_id": str(account.id), **payload}


def _hubspot_create_contact_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    email = _coerce_str(arguments.get("email")).strip()
    if not email:
        return _integration_error("hubspot_create_contact", error_code="validation_error", hint="email is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.HUBSPOT, tool="hubspot_create_contact",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "hubspot_create_contact")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        payload = hubspot_create_contact(
            access_token,
            email=email,
            first_name=_coerce_str(arguments.get("first_name")).strip(),
            last_name=_coerce_str(arguments.get("last_name")).strip(),
            phone=_coerce_str(arguments.get("phone")).strip(),
            company=_coerce_str(arguments.get("company")).strip(),
            job_title=_coerce_str(arguments.get("job_title")).strip(),
        )
    except HubSpotApiError as exc:
        return _integration_error("hubspot_create_contact", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "hubspot_create_contact", "status": "ok", "integration_account_id": str(account.id), **payload}


def _hubspot_search_deals_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _integration_error("hubspot_search_deals", error_code="missing_query", hint="query is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.HUBSPOT, tool="hubspot_search_deals",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "hubspot_search_deals")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = hubspot_search_deals(access_token, query=query, max_results=max_results)
    except HubSpotApiError as exc:
        return _integration_error("hubspot_search_deals", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "hubspot_search_deals", "status": "ok", "integration_account_id": str(account.id), **payload}
