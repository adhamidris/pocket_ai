from __future__ import annotations

import logging
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


class _ResponseTextExtractor:
    """
    Incrementally parses the JSON string to stream the `response_text` field.

    The extractor scans for the `"response_text"` key, then decodes the string
    value while respecting JSON escape sequences and unicode escapes. As soon as
    new characters are available, they are emitted via the provided callback.
    """

    TARGET = '"response_text"'

    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self._key_index = 0
        self._state = "search"  # search -> post_key -> seek_quote -> in_string -> done
        self._in_string = False
        self._escape = False
        self._unicode_digits: list[str] | None = None
        self._buffer: list[str] = []
        self._completed = False

    def feed(self, chunk: str) -> None:
        if self._completed or not chunk:
            return
        for ch in chunk:
            if self._completed:
                break
            self._consume(ch)
        self._flush()

    def flush(self) -> None:
        self._flush()

    def _consume(self, ch: str) -> None:
        if self._completed:
            return
        if self._in_string:
            self._consume_string(ch)
            return
        if self._state == "search":
            self._scan_key(ch)
            return
        if self._state == "post_key":
            if ch == ":":
                self._state = "seek_quote"
            elif ch in " \t\r\n":
                return
            else:
                self._reset()
                self._scan_key(ch)
            return
        if self._state == "seek_quote":
            if ch == '"':
                self._in_string = True
            elif ch in " \t\r\n":
                return
            else:
                self._reset()
                self._scan_key(ch)

    def _consume_string(self, ch: str) -> None:
        if self._unicode_digits is not None:
            self._unicode_digits.append(ch)
            if len(self._unicode_digits) == 4:
                try:
                    codepoint = int("".join(self._unicode_digits), 16)
                    self._buffer.append(chr(codepoint))
                except ValueError:
                    pass
                self._unicode_digits = None
            return
        if self._escape:
            self._escape = False
            if ch == "u":
                self._unicode_digits = []
                return
            self._buffer.append(self._escape_map(ch))
            return
        if ch == "\\":
            self._escape = True
            return
        if ch == '"':
            self._in_string = False
            self._completed = True
            return
        self._buffer.append(ch)

    def _scan_key(self, ch: str) -> None:
        target = self.TARGET
        if ch == target[self._key_index]:
            self._key_index += 1
            if self._key_index == len(target):
                self._state = "post_key"
        else:
            self._key_index = 1 if ch == target[0] else 0

    def _reset(self) -> None:
        self._key_index = 0
        self._state = "search"

    def _flush(self) -> None:
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        try:
            self.emit(text)
        except Exception:  # pragma: no cover - safeguard user callbacks
            logger.exception("Streaming callback failed while emitting response_text delta.")

    @staticmethod
    def _escape_map(ch: str) -> str:
        mapping = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        return mapping.get(ch, ch)


def _emit_stream_chunks(callback: Callable[[str], None], text: str, *, chunk_size: int = 1) -> None:
    """
    Emit a text payload to a streaming callback in word-safe chunks.

    Mirrors the SSE chunking strategy used by the chat portal so the MCP
    providers can surface incremental deltas even when the underlying HTTP
    response is non-streaming.
    """

    clean = (text or "").strip()
    if not clean:
        return
    words = clean.split()
    if not words:
        return

    current: list[str] = []
    current_len = 0
    for word in words:
        if not current:
            current.append(word)
            current_len = len(word)
            continue
        projected = current_len + 1 + len(word)
        if projected <= chunk_size:
            current.append(word)
            current_len = projected
        else:
            try:
                callback(" ".join(current))
            except Exception:  # pragma: no cover - safeguard user callbacks
                logger.exception("Streaming callback failed while emitting chunk.")
            current = [word]
            current_len = len(word)

    if current:
        try:
            callback(" ".join(current))
        except Exception:  # pragma: no cover - safeguard user callbacks
            logger.exception("Streaming callback failed while emitting final chunk.")


class _HttpxLineStream:
    """
    Adapter to present httpx.iter_lines() as a file-like object with readline().
    """

    def __init__(self, iterator: Iterable[bytes] | Iterable[str]) -> None:
        self._iterator = iter(iterator)

    def readline(self) -> bytes:
        try:
            line = next(self._iterator)
        except StopIteration:
            return b""
        if isinstance(line, str):
            # httpx.iter_lines() yields strings without trailing newlines; preserve
            # blank lines by emitting a newline so the SSE parser can flush buffers.
            return (line + "\n").encode("utf-8")
        # For byte lines, also ensure a newline delimiter so blank lines are honored.
        return line if line.endswith(b"\n") else line + b"\n"


def _iter_sse_events(stream) -> Iterable[str]:
    """
    Yield decoded payload strings from a server-sent events stream.

    The OpenAI-compatible APIs send newline-delimited `data:` entries followed by
    a blank line. Each yielded string corresponds to the bytes after `data:`
    (with `[DONE]` filtered out).
    """

    buffer: list[str] = []
    while True:
        raw_line = stream.readline()
        if not raw_line:
            if buffer:
                yield "\n".join(buffer)
            break
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            value = value.rstrip("\r\n")
            if value == "[DONE]":
                if buffer:
                    yield "\n".join(buffer)
                    buffer.clear()
                break
            if value:
                buffer.append(value)
            continue
        if not line.strip():
            if buffer:
                yield "\n".join(buffer)
                buffer.clear()
            continue
