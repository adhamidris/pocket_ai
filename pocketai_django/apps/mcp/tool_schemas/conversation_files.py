from __future__ import annotations

from typing import Mapping

from .base import _function_schema


CONVERSATION_FILE_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (

    _function_schema(
        name="search_conversation_files",
        description="Search files uploaded in this chat session (PDFs).",
        properties={
            "query": {
                "type": "string",
                "description": "What to look for in uploaded files.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of snippets to return (1-8).",
                "minimum": 1,
                "maximum": 8,
                "default": 5,
            },
        },
        required=("query",),
    ),
    _function_schema(
        name="read_conversation_file",
        description=(
            "Read extracted text from uploaded chat files. "
            "In agentic mode prefer ids[] from search_conversation_files."
        ),
        properties={
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of chunk IDs from search_conversation_files results (agentic mode).",
                "minItems": 1,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum total characters to return across all ids.",
                "minimum": 500,
                "maximum": 20000,
                "default": 8000,
            },
        },
        required=("ids",),
    ),
    _function_schema(
        name="pdf_generate",
        description=(
            "Generate a PDF from provided text/markdown and attach it to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "content": {"type": "string", "description": "Main content to render into the PDF."},
            "title": {"type": "string", "description": "Optional title displayed at the top."},
            "filename": {
                "type": "string",
                "description": "Optional output filename (e.g., 'summary.pdf').",
            },
            "format": {
                "type": "string",
                "enum": ["text", "markdown"],
                "default": "markdown",
                "description": "Input content format. Markdown is rendered as plain text (no HTML).",
            },
            "page_size": {
                "type": "string",
                "enum": ["letter", "a4"],
                "default": "letter",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {"type": "string"},
                },
            },
        },
        required=("content",),
    ),
    _function_schema(
        name="pdf_merge",
        description=(
            "Merge multiple PDFs (uploaded or generated in this chat) and attach the merged PDF to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "file_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "description": "List of PDF file IDs to merge (ConversationFile IDs).",
            },
            "filename": {"type": "string", "description": "Optional output filename (e.g., 'merged.pdf')."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_ids",),
    ),
    _function_schema(
        name="pdf_extract_pages",
        description=(
            "Extract specific pages from a PDF and attach the new PDF to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "file_id": {"type": "string", "description": "PDF file ID (ConversationFile ID)."},
            "pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "minItems": 1,
                "description": "1-based page numbers to extract.",
            },
            "filename": {"type": "string", "description": "Optional output filename (e.g., 'pages.pdf')."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_id", "pages"),
    ),
    _function_schema(
        name="pdf_extract_text",
        description="Extract text from a PDF (optionally specific pages).",
        properties={
            "file_id": {"type": "string", "description": "PDF file ID (ConversationFile ID)."},
            "pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Optional list of 1-based page numbers to extract.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 500,
                "maximum": 50000,
                "default": 12000,
                "description": "Maximum characters to return.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_id",),
    ),
)
