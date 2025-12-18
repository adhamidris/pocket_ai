from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from django.conf import settings


def normalize_identifier_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.strip("`\"'")
    return "".join(text.split()).lower()


def _stable_digest(value: str) -> bytes:
    return hashlib.blake2b(value.encode("utf-8", errors="ignore"), digest_size=16).digest()


@dataclass(frozen=True)
class BloomSpec:
    bits: int
    hashes: int


class BloomFilter:
    def __init__(self, *, bits: int, hashes: int, bitset: bytearray | None = None) -> None:
        bits = int(bits)
        hashes = int(hashes)
        if bits <= 0:
            raise ValueError("bits must be positive")
        if hashes <= 0:
            raise ValueError("hashes must be positive")
        self.bits = bits
        self.hashes = hashes
        byte_len = (bits + 7) // 8
        self._bitset = bitset if bitset is not None else bytearray(byte_len)
        if len(self._bitset) != byte_len:
            raise ValueError("bitset length mismatch")

    @classmethod
    def from_bytes(cls, *, bits: int, hashes: int, data: bytes) -> BloomFilter:
        return cls(bits=bits, hashes=hashes, bitset=bytearray(data))

    def to_bytes(self) -> bytes:
        return bytes(self._bitset)

    def add(self, value: str) -> None:
        if not value:
            return
        h1, h2 = _double_hash(value)
        for idx in range(self.hashes):
            pos = (h1 + idx * h2) % self.bits
            _set_bit(self._bitset, pos)

    def maybe_contains(self, value: str) -> bool:
        if not value:
            return False
        h1, h2 = _double_hash(value)
        for idx in range(self.hashes):
            pos = (h1 + idx * h2) % self.bits
            if not _get_bit(self._bitset, pos):
                return False
        return True


def _double_hash(value: str) -> tuple[int, int]:
    digest = _stable_digest(value)
    h1 = int.from_bytes(digest[:8], "big", signed=False)
    h2 = int.from_bytes(digest[8:], "big", signed=False) or 0x9E3779B185EBCA87
    return h1, h2


def _set_bit(buf: bytearray, bit: int) -> None:
    byte_index = bit // 8
    mask = 1 << (bit % 8)
    buf[byte_index] |= mask


def _get_bit(buf: bytearray, bit: int) -> bool:
    byte_index = bit // 8
    mask = 1 << (bit % 8)
    return bool(buf[byte_index] & mask)


def bloom_spec_for_items(expected_items: int) -> BloomSpec:
    bits_per_item = int(getattr(settings, "DATASET_KEY_INDEX_BITS_PER_ITEM", 10) or 10)
    bits_per_item = max(4, min(24, bits_per_item))
    expected = max(1, int(expected_items))
    bits = max(1024, expected * bits_per_item)
    hashes = max(2, int(round(bits_per_item * math.log(2))))
    return BloomSpec(bits=bits, hashes=hashes)


def write_bloom_filter(path: Path, bloom: BloomFilter) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bloom.to_bytes()
    path.write_bytes(payload)
    return len(payload)


def resolve_key_index_storage_path(*, dataset_rel_path: str, column_name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in (column_name or "").strip().lower()).strip("-") or "key"
    safe = safe[:60] if len(safe) > 60 else safe
    return f"{dataset_rel_path}.keyindex.{safe}.bf"


def key_indexing_enabled() -> bool:
    return str(getattr(settings, "DATASET_KEY_INDEX_ENABLED", "true")).lower() in {"1", "true", "yes"}


def should_build_dataset_key_indexes(upload_metadata: Mapping[str, Any] | None = None) -> bool:
    if not key_indexing_enabled():
        return False
    meta = upload_metadata if isinstance(upload_metadata, Mapping) else {}
    dataset = meta.get("dataset")
    if isinstance(dataset, Mapping) and dataset.get("enabled"):
        return True
    return False

