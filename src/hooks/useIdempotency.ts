import { useCallback, useRef } from "react";

type CacheEntry = {
  hash: string;
  key: string;
};

const normalizeObject = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map((item) => normalizeObject(item));
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => a.localeCompare(b));
    const normalized: Record<string, unknown> = {};
    for (const [k, v] of entries) {
      normalized[k] = normalizeObject(v);
    }
    return normalized;
  }
  return value;
};

const stableStringify = (value: unknown) => {
  const normalized = normalizeObject(value);
  try {
    return JSON.stringify(normalized);
  } catch {
    return String(normalized);
  }
};

const hashString = (input: string): string => {
  let hash1 = 0x811c9dc5;
  let hash2 = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    const char = input.charCodeAt(i);
    hash1 ^= char;
    hash1 = Math.imul(hash1, 0x01000193);
    hash1 >>>= 0;
    hash2 ^= (char << 1) | (char >>> 15);
    hash2 = Math.imul(hash2, 0x01000193);
    hash2 >>>= 0;
  }
  const part1 = hash1.toString(16).padStart(8, "0");
  const part2 = hash2.toString(16).padStart(8, "0");
  return `${part1}${part2}`;
};

export function useIdempotency() {
  const cacheRef = useRef<Record<string, CacheEntry>>({});

  return useCallback((step: string, payload: unknown) => {
    const payloadString = stableStringify(payload ?? {});
    const payloadHash = hashString(payloadString).slice(0, 32);
    const cached = cacheRef.current[step];
    if (cached && cached.hash === payloadHash) {
      return cached.key;
    }
    const keyBase = `${step}-${payloadHash}`;
    const trimmed = keyBase.length > 60 ? keyBase.slice(0, 60) : keyBase;
    const key = trimmed.replace(/[^A-Za-z0-9_.:-]/g, "");
    cacheRef.current[step] = { hash: payloadHash, key };
    return key;
  }, []);
}

