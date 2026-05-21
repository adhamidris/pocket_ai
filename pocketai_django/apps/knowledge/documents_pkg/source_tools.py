from __future__ import annotations

import csv
import io
import re
import time
from typing import BinaryIO, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from apps.knowledge.documents_pkg.contracts import (
    CsvPreview,
    CsvPreviewError,
    DocumentScrapeError,
    ScrapedDocument,
)


def scrape_document_source(
    *,
    url: str,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
) -> ScrapedDocument:
    """
    Fetch a remote URL and return a text representation, trimming aggressively for speed.
    """

    normalized = url.strip()
    parsed = urlparse(normalized)
    if not parsed.scheme or parsed.scheme not in {"http", "https"}:
        raise DocumentScrapeError("Enter a valid HTTP or HTTPS URL.")

    headers = {
        "User-Agent": "PocketAI-KnowledgeBot/1.0 (+https://pocket.ai)",
        "Accept": "text/html,text/plain,application/json;q=0.8,*/*;q=0.1",
    }
    request = Request(normalized, headers=headers)
    start = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            collected: list[bytes] = []
            total = 0
            truncated = False
            while True:
                if total >= max_bytes:
                    truncated = True
                    break
                chunk_size = min(64 * 1024, max_bytes - total)
                if chunk_size <= 0:
                    truncated = True
                    break
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                collected.append(chunk)
            raw_bytes = b"".join(collected)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            headers_obj = getattr(response, "headers", {})
            content_type = headers_obj.get("content-type", "") if hasattr(headers_obj, "get") else ""
            text = _bytes_to_text(raw_bytes, content_type)
            preview = text[:1200]
            word_count = len(text.split())
            content_length_header = headers_obj.get("content-length") if hasattr(headers_obj, "get") else None
            try:
                content_length = int(content_length_header) if content_length_header is not None else len(raw_bytes)
            except (TypeError, ValueError):
                content_length = len(raw_bytes)
            status_code = getattr(response, "status", None)
            if status_code is None:
                status_code = response.getcode()
            return ScrapedDocument(
                url=normalized,
                final_url=response.geturl(),
                status_code=status_code,
                content_type=content_type,
                elapsed_ms=elapsed_ms,
                content_length=content_length,
                truncated=truncated,
                text=text,
                preview=preview,
                word_count=word_count,
            )
    except HTTPError as exc:
        message = f"HTTP error {exc.code}: {exc.reason}"
        raise DocumentScrapeError(message) from exc
    except URLError as exc:
        raise DocumentScrapeError(f"Unable to fetch the URL: {exc.reason}") from exc


def preview_csv_upload(
    file_obj: BinaryIO,
    *,
    max_rows: int = 50,
    max_bytes: int = 1_000_000,
    encoding: str = "utf-8",
) -> CsvPreview:
    """
    Stream just enough of a CSV upload to preview headers/content without exhausting memory.
    """

    if max_rows < 1:
        raise CsvPreviewError("max_rows must be >= 1")

    buffer = io.BytesIO()
    total = 0
    truncated = False

    for chunk in _iter_chunks(file_obj):
        if not chunk:
            continue
        next_total = total + len(chunk)
        if next_total > max_bytes:
            buffer.write(chunk[: max_bytes - total])
            truncated = True
            break
        buffer.write(chunk)
        total = next_total
        if total >= max_bytes:
            truncated = True
            break

    if hasattr(file_obj, "seek"):
        try:
            file_obj.seek(0)
        except (OSError, io.UnsupportedOperation):
            pass

    try:
        text = buffer.getvalue().decode(encoding, errors="ignore")
    except UnicodeDecodeError as exc:  # pragma: no cover - defensive
        raise CsvPreviewError("Unable to decode CSV using the provided encoding.") from exc

    sample = io.StringIO(text)
    try:
        dialect = csv.Sniffer().sniff(sample.read(2048)) if text else csv.excel
    except csv.Error:
        dialect = csv.excel
    sample.seek(0)

    reader = csv.reader(sample, dialect)
    rows: list[tuple[str, ...]] = []
    columns: tuple[str, ...] = ()

    try:
        columns = tuple(next(reader))
    except StopIteration:
        columns = ()

    for idx, row in enumerate(reader, start=1):
        if idx > max_rows:
            truncated = True
            break
        rows.append(tuple(row))

    return CsvPreview(
        columns=columns,
        rows=tuple(rows),
        row_count=len(rows),
        truncated=truncated,
        dialect={
            "delimiter": getattr(dialect, "delimiter", ","),
            "quotechar": getattr(dialect, "quotechar", '"'),
            "escapechar": getattr(dialect, "escapechar", None),
        },
    )


def _bytes_to_text(raw: bytes, content_type: str) -> str:
    """
    Convert raw bytes into plain text, stripping HTML when necessary.
    """
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="ignore")

    # If HTML, strip script/style tags, then all tags
    if "html" in (content_type or "").lower():
        # strip <script>...</script> and <style>...</style> (case-insensitive, dot matches newline)
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        # strip any remaining HTML tags
        text = re.sub(r"(?s)<[^>]+>", " ", text)

    # collapse any whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text



def _iter_chunks(file_obj: BinaryIO) -> Iterable[bytes]:
    """
    Iterate over chunks for UploadedFile (with .chunks) or a standard file object.
    """

    if hasattr(file_obj, "chunks"):
        yield from file_obj.chunks()  # type: ignore[attr-defined]
        return
    while True:
        chunk = file_obj.read(64 * 1024)
        if not chunk:
            break
        yield chunk
