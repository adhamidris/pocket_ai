from __future__ import annotations

from typing import Mapping

from .base import _function_schema


CLOUD_FILE_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — Google Drive
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="drive_search_files",
        description="Search files in the connected Google Drive.",
        properties={
            "query": {"type": "string", "description": "Search query (file name or content)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    _function_schema(
        name="drive_get_file",
        description="Get file content from Google Drive (text-based files). Returns metadata for binary files.",
        properties={
            "file_id": {"type": "string", "description": "Google Drive file ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("file_id",),
    ),
    _function_schema(
        name="drive_list_files",
        description="List files in a Google Drive folder (or root if no folder specified).",
        properties={
            "folder_id": {"type": "string", "description": "Google Drive folder ID (optional, omit for root)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — OneDrive
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="onedrive_search_files",
        description="Search files in the connected OneDrive.",
        properties={
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    _function_schema(
        name="onedrive_get_file",
        description="Get file content from OneDrive (text-based files). Returns metadata for binary files.",
        properties={
            "file_id": {"type": "string", "description": "OneDrive item ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("file_id",),
    ),
    _function_schema(
        name="onedrive_list_files",
        description="List files in a OneDrive folder (or root if no folder specified).",
        properties={
            "folder_id": {"type": "string", "description": "OneDrive folder ID (optional, omit for root)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
)
