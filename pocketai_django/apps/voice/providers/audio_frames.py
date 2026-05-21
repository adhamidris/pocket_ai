from __future__ import annotations

import re
from typing import AsyncIterator


def frame_bytes_for_output(output_format: str, frame_ms: int = 20) -> int:
    """
    Compute frame size (bytes) for a given output format and frame duration.

    Twilio Media Streams expects 20ms frames at the sample rate.
    For µ-law audio, bytes-per-sample is 1.
    """

    sample_rate = 8000
    if output_format:
        match = re.search(r"(\d{4,6})", output_format)
        if match:
            try:
                sample_rate = int(match.group(1))
            except ValueError:
                sample_rate = 8000
    frame_bytes = int(sample_rate * (frame_ms / 1000.0))
    return max(1, frame_bytes)


async def iter_audio_frames(
    audio_iter: AsyncIterator[bytes],
    *,
    output_format: str,
    frame_ms: int = 20,
    pad_final: bool = True,
    pad_byte: bytes = b"\xff",
) -> AsyncIterator[bytes]:
    """
    Yield fixed-size audio frames from an async byte stream.
    """

    frame_size = frame_bytes_for_output(output_format, frame_ms=frame_ms)
    buffer = bytearray()
    async for chunk in audio_iter:
        if not chunk:
            continue
        buffer.extend(chunk)
        while len(buffer) >= frame_size:
            frame = bytes(buffer[:frame_size])
            del buffer[:frame_size]
            yield frame
    if buffer:
        if pad_final and len(buffer) < frame_size:
            pad_len = frame_size - len(buffer)
            yield bytes(buffer) + pad_byte * pad_len
        else:
            yield bytes(buffer)
