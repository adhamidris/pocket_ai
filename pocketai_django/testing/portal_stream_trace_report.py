#!/usr/bin/env python3
"""
Portal Stream Trace Report

Reads JSONL traces emitted by PortalStreamTrace (worker + SSE) and prints a
quick summary to pinpoint buffering / batching sources.

Usage:
  python testing/portal_stream_trace_report.py --turn-id <uuid>
  python testing/portal_stream_trace_report.py --turn-id <uuid> --dir /tmp/pocketai/portal_stream_traces
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from dataclasses import dataclass


def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    values_sorted = sorted(values)
    idx = int(max(0, min(len(values_sorted) - 1, round((p / 100.0) * (len(values_sorted) - 1)))))
    return int(values_sorted[idx])


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return int(default)


@dataclass
class TraceEvent:
    component: str
    event: str
    t_ms: int
    t_epoch_ms: int
    data: dict


def _load_events(paths: list[str]) -> list[TraceEvent]:
    out: list[TraceEvent] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    out.append(
                        TraceEvent(
                            component=str(obj.get("component") or "trace"),
                            event=str(obj.get("event") or "event"),
                            t_ms=_safe_int(obj.get("t_ms")),
                            t_epoch_ms=_safe_int(obj.get("t_epoch_ms")),
                            data={k: v for k, v in obj.items() if k not in {"component", "event", "t_ms", "t_epoch_ms"}},
                        )
                    )
        except FileNotFoundError:
            continue
    return out


def _summarize_worker(events: list[TraceEvent]) -> None:
    worker = [e for e in events if e.component == "worker"]
    if not worker:
        print("- worker: (no events)")
        return

    deltas = [e for e in worker if e.event == "delta.in"]
    delta_lens = [_safe_int(e.data.get("len")) for e in deltas if _safe_int(e.data.get("len")) > 0]
    print(f"- worker: events={len(worker)} deltas={len(delta_lens)}")
    if delta_lens:
        print(
            f"  - delta_len: total={sum(delta_lens)} mean={int(statistics.mean(delta_lens))} "
            f"p50={_pct(delta_lens,50)} p95={_pct(delta_lens,95)} max={max(delta_lens)}"
        )

    appended = [e for e in worker if e.event == "event.appended"]
    append_ms = [_safe_int(e.data.get("append_ms")) for e in appended if _safe_int(e.data.get("append_ms")) >= 0]
    if append_ms:
        print(
            f"  - append_ms: mean={int(statistics.mean(append_ms))} p95={_pct(append_ms,95)} max={max(append_ms)}"
        )

    finals = [e for e in worker if e.event.startswith("turn.finalize.")]
    if finals:
        last = sorted(finals, key=lambda x: x.t_epoch_ms)[-1]
        print(f"  - finalize: last_event={last.event} data={{{', '.join(f'{k}={last.data.get(k)}' for k in ('blocks_source','blocks','body_len','body_len_pre_sanitize') if k in last.data)}}}")


def _summarize_sse(events: list[TraceEvent]) -> None:
    sse = [e for e in events if e.component == "sse"]
    if not sse:
        print("- sse: (no events)")
        return

    batches = [e for e in sse if e.event == "sse.batch"]
    batch_sizes = [_safe_int(e.data.get("events")) for e in batches if _safe_int(e.data.get("events")) >= 0]
    batch_text = [_safe_int(e.data.get("text_chars")) for e in batches if _safe_int(e.data.get("text_chars")) >= 0]
    drains = [e for e in sse if e.event == "sse.drain_batch"]
    drain_sizes = [_safe_int(e.data.get("events")) for e in drains if _safe_int(e.data.get("events")) >= 0]

    print(f"- sse: events={len(sse)} batches={len(batches)} drains={len(drains)}")
    if batch_sizes:
        print(
            f"  - batch_events: total={sum(batch_sizes)} mean={int(statistics.mean(batch_sizes))} "
            f"p50={_pct(batch_sizes,50)} p95={_pct(batch_sizes,95)} max={max(batch_sizes)}"
        )
    if batch_text:
        print(
            f"  - batch_text_chars: total={sum(batch_text)} mean={int(statistics.mean(batch_text))} "
            f"p50={_pct(batch_text,50)} p95={_pct(batch_text,95)} max={max(batch_text)}"
        )
    if drain_sizes:
        print(
            f"  - drain_events: total={sum(drain_sizes)} mean={int(statistics.mean(drain_sizes))} "
            f"p95={_pct(drain_sizes,95)} max={max(drain_sizes)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Portal stream trace report")
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--dir", default="/tmp/pocketai/portal_stream_traces")
    args = parser.parse_args()

    turn_id = str(args.turn_id).strip()
    directory = os.path.abspath(os.path.expanduser(str(args.dir)))
    pattern = os.path.join(directory, f"{turn_id}.*.jsonl")
    paths = sorted(glob.glob(pattern))

    print("")
    print("Portal Stream Trace Report")
    print(f"- turn_id: {turn_id}")
    print(f"- dir: {directory}")
    print(f"- files: {len(paths)}")
    for p in paths[:6]:
        print(f"  - {os.path.basename(p)}")
    if len(paths) > 6:
        print(f"  - (+{len(paths) - 6} more)")

    events = _load_events(paths)
    print(f"- trace_events: {len(events)}")
    print("")
    _summarize_worker(events)
    _summarize_sse(events)
    print("")

    if not events:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

