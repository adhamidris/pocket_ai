from __future__ import annotations

from typing import Mapping

from apps.accounts.models import IntegrationType
from apps.conversations.models import Conversation
from apps.integrations.providers.microsoft_onedrive import (
    OneDriveApiError,
    onedrive_get_file_content,
    onedrive_list_files,
    onedrive_search_files,
)

from ...types import ToolExecutionContext
from .shared import (
    _coerce_str,
    _get_integration_access_token,
    _integration_error,
    _resolve_integration_account_for_tool,
)


def _onedrive_search_files_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return _integration_error("onedrive_search_files", error_code="missing_query", hint="query is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.ONEDRIVE, tool="onedrive_search_files",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "onedrive_search_files")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        max_results = int(arguments.get("max_results") or 10)
    except (TypeError, ValueError):
        max_results = 10

    try:
        payload = onedrive_search_files(access_token, query=query, max_results=max_results)
    except OneDriveApiError as exc:
        return _integration_error("onedrive_search_files", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "onedrive_search_files", "status": "ok", "integration_account_id": str(account.id), **payload}


def _onedrive_get_file_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    file_id = _coerce_str(arguments.get("file_id")).strip()
    if not file_id:
        return _integration_error("onedrive_get_file", error_code="missing_file_id", hint="file_id is required.")

    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.ONEDRIVE, tool="onedrive_get_file",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "onedrive_get_file")
    if token_error:
        return token_error
    assert access_token is not None

    try:
        payload = onedrive_get_file_content(access_token, file_id)
    except OneDriveApiError as exc:
        return _integration_error("onedrive_get_file", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "onedrive_get_file", "status": "ok", "integration_account_id": str(account.id), **payload}


def _onedrive_list_files_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    account, error = _resolve_integration_account_for_tool(
        integration_type=IntegrationType.ONEDRIVE, tool="onedrive_list_files",
        arguments=arguments, conversation=conversation,
    )
    if error:
        return error
    assert account is not None

    access_token, token_error = _get_integration_access_token(account, "onedrive_list_files")
    if token_error:
        return token_error
    assert access_token is not None

    folder_id = _coerce_str(arguments.get("folder_id")).strip() or None
    try:
        max_results = int(arguments.get("max_results") or 20)
    except (TypeError, ValueError):
        max_results = 20

    try:
        payload = onedrive_list_files(access_token, folder_id=folder_id, max_results=max_results)
    except OneDriveApiError as exc:
        return _integration_error("onedrive_list_files", error_code="provider_error", hint=str(exc)[:200])

    return {"tool": "onedrive_list_files", "status": "ok", "integration_account_id": str(account.id), **payload}
