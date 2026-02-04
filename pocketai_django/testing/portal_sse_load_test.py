#!/usr/bin/env python3
"""
Portal SSE Load Test (Phase 6)

This script opens many concurrent SSE connections and measures:
- time to first event
- events/sec
- disconnect/error rate

Examples:
  python testing/portal_sse_load_test.py --base-url http://localhost:8000 --session-token <token> --clients 500 --duration 60 --stream session
  python testing/portal_sse_load_test.py --base-url http://localhost:8000 --session-token <token> --clients 200 --duration 30 --stream turn --turn-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass

import httpx


@dataclass
class ClientResult:
    ok: bool
    first_event_ms: int | None
    events: int
    error: str | None = None


def _build_url(*, base_url: str, stream: str, session_token: str, turn_id: str | None) -> str:
    base = base_url.rstrip("/")
    if stream == "session":
        return f"{base}/api/chat/events/?session_token={session_token}"
    if stream == "turn":
        if not turn_id:
            raise ValueError("--turn-id is required for --stream turn")
        return f"{base}/api/chat/turns/{turn_id}/events/?session_token={session_token}"
    raise ValueError(f"unknown stream: {stream}")


async def _run_client(*, client: httpx.AsyncClient, url: str, duration_s: float) -> ClientResult:
    started = time.perf_counter()
    first_event_ms: int | None = None
    events = 0

    event_id = None
    event_name = None
    data_buf: list[str] = []

    try:
        async with client.stream("GET", url, timeout=None, headers={"Accept": "text/event-stream"}) as resp:
            resp.raise_for_status()
            deadline = time.monotonic() + max(0.1, float(duration_s))

            async for line in resp.aiter_lines():
                if time.monotonic() >= deadline:
                    break

                if not line:
                    # Event boundary
                    if event_name and data_buf:
                        _data_text = "\n".join(data_buf)
                        try:
                            json.loads(_data_text)
                        except Exception:
                            pass
                        events += 1
                        if first_event_ms is None:
                            first_event_ms = int(max(0.0, (time.perf_counter() - started) * 1000.0))
                    event_id = None
                    event_name = None
                    data_buf = []
                    continue

                if line.startswith(":"):
                    # comment / keepalive
                    continue
                if line.startswith("id:"):
                    event_id = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("data:"):
                    data_buf.append(line.split(":", 1)[1].lstrip())
                    continue

        return ClientResult(ok=True, first_event_ms=first_event_ms, events=events)
    except Exception as exc:
        return ClientResult(ok=False, first_event_ms=first_event_ms, events=events, error=str(exc))


def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    values_sorted = sorted(values)
    idx = int(max(0, min(len(values_sorted) - 1, round((p / 100.0) * (len(values_sorted) - 1)))))
    return values_sorted[idx]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Portal SSE load test")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--session-token", required=True)
    parser.add_argument("--turn-id", default=None)
    parser.add_argument("--stream", choices=["session", "turn"], default="session")
    parser.add_argument("--clients", type=int, default=200)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--stagger-ms", type=int, default=5, help="Delay between opening connections to reduce thundering herd")
    args = parser.parse_args()

    url = _build_url(base_url=args.base_url, stream=args.stream, session_token=args.session_token, turn_id=args.turn_id)
    client_count = max(1, int(args.clients))
    duration_s = max(0.5, float(args.duration))
    stagger_ms = max(0, int(args.stagger_ms))

    limits = httpx.Limits(max_connections=client_count + 50, max_keepalive_connections=client_count + 50)
    async with httpx.AsyncClient(limits=limits) as http:
        tasks: list[asyncio.Task[ClientResult]] = []
        for _ in range(client_count):
            tasks.append(asyncio.create_task(_run_client(client=http, url=url, duration_s=duration_s)))
            if stagger_ms:
                await asyncio.sleep(stagger_ms / 1000.0)

        results = await asyncio.gather(*tasks)

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    firsts = [r.first_event_ms for r in ok if r.first_event_ms is not None]
    firsts_int = [int(x) for x in firsts if isinstance(x, int)]
    events_total = sum(r.events for r in ok)

    print("")
    print("Portal SSE Load Test Results")
    print(f"- stream: {args.stream}")
    print(f"- url: {url}")
    print(f"- clients: {client_count}")
    print(f"- duration_s: {duration_s:.2f}")
    print(f"- ok: {len(ok)}")
    print(f"- failed: {len(failed)}")
    if firsts_int:
        print(f"- first_event_ms: mean={int(statistics.mean(firsts_int))} p50={_pct(firsts_int,50)} p95={_pct(firsts_int,95)} p99={_pct(firsts_int,99)}")
    else:
        print("- first_event_ms: (no events received)")
    print(f"- events_total: {events_total}")
    print(f"- events_per_sec_total: {events_total / max(0.001, duration_s):.2f}")
    if failed:
        sample = failed[0]
        print(f"- sample_error: {sample.error}")
    print("")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

