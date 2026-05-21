from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse

from apps.conversations.models import ConversationFile
from apps.conversations.portal_files.errors import PortalFileError

_FILENAME_STRIP = re.compile(r"[^A-Za-z0-9._ -]+")


def _media_root() -> Path:
    root = Path(getattr(settings, "MEDIA_ROOT", "") or "")
    if not root:
        raise PortalFileError("MEDIA_ROOT is not configured.")
    return root.resolve()


def normalize_filename(filename: str) -> str:
    raw = (filename or "").strip()
    base = Path(raw).name if raw else ""
    base = base.replace("\x00", "").strip()
    if not base:
        return "upload.bin"
    cleaned = _FILENAME_STRIP.sub("_", base).strip("._ ")
    if not cleaned:
        cleaned = "upload.bin"
    if len(cleaned) > 180:
        suffix = Path(cleaned).suffix
        stem = Path(cleaned).stem[: max(1, 180 - len(suffix))]
        cleaned = f"{stem}{suffix}"
    return cleaned


def guess_content_type(filename: str, provided: str | None = None) -> str:
    provided_clean = (provided or "").strip()
    if provided_clean:
        return provided_clean
    guessed = mimetypes.guess_type(filename)[0]
    return guessed or "application/octet-stream"


def portal_storage_paths(*, business_id: uuid.UUID, conversation_id: uuid.UUID, file_id: uuid.UUID, filename: str) -> tuple[Path, Path]:
    media_root = _media_root()
    safe_filename = normalize_filename(filename)
    rel_dir = Path("portal_files") / str(business_id) / str(conversation_id) / str(file_id)
    rel_path = rel_dir / safe_filename
    abs_path = (media_root / rel_path).resolve()
    try:
        abs_path.relative_to(media_root)
    except ValueError as exc:
        raise PortalFileError("Invalid portal file path.") from exc
    return abs_path, rel_path


def write_uploaded_bytes(uploaded_file, destination: Path) -> tuple[int, str]:
    """
    Stream an uploaded file to disk while computing sha256.

    Returns: (size_bytes, checksum_sha256)
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    with destination.open("wb") as f:
        for chunk in uploaded_file.chunks():
            if not chunk:
                continue
            size += len(chunk)
            hasher.update(chunk)
            f.write(chunk)
    return size, hasher.hexdigest()


def write_bytes(payload: bytes, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    with destination.open("wb") as f:
        if payload:
            size = len(payload)
            hasher.update(payload)
            f.write(payload)
    return size, hasher.hexdigest()


def open_portal_file_response(*, file: ConversationFile, download: bool = True) -> FileResponse:
    media_root = _media_root()
    storage_path = Path(file.storage_path)
    absolute = (media_root / storage_path).resolve()
    try:
        absolute.relative_to(media_root)
    except ValueError as exc:
        raise PortalFileError("Invalid storage path.") from exc
    if not absolute.exists():
        raise FileNotFoundError(file.storage_path)
    filename = file.filename or absolute.name
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = FileResponse(absolute.open("rb"), as_attachment=download, filename=filename)
    response["Content-Type"] = content_type
    return response


def resolve_portal_file_path(file: ConversationFile) -> Path:
    media_root = _media_root()
    storage_path = Path(file.storage_path)
    absolute = (media_root / storage_path).resolve()
    try:
        absolute.relative_to(media_root)
    except ValueError as exc:
        raise PortalFileError("Invalid storage path.") from exc
    return absolute
