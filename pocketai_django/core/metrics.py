from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Mapping, Tuple


logger = logging.getLogger("core.metrics")


class LatencyMonitor:
    def __init__(self, window_size: int = 64, log_interval_seconds: int = 60) -> None:
        self.window_size = window_size
        self.log_interval_seconds = log_interval_seconds
        self._samples: dict[Tuple[str, Tuple[tuple[str, str], ...]], deque[float]] = defaultdict(
            lambda: deque(maxlen=self.window_size)
        )
        self._last_logged: dict[Tuple[str, Tuple[tuple[str, str], ...]], float] = {}

    def observe(self, stage: str, duration_ms: int | float, tags: Mapping[str, str] | None = None) -> None:
        if duration_ms is None:
            return
        try:
            value = float(duration_ms)
        except (TypeError, ValueError):
            return
        if value < 0:
            return
        key = self._key(stage, tags)
        window = self._samples[key]
        window.append(value)
        now = time.time()
        last_logged = self._last_logged.get(key, 0.0)
        if len(window) == window.maxlen and (now - last_logged) >= self.log_interval_seconds:
            ordered = sorted(window)
            p50 = ordered[len(ordered) // 2]
            idx_95 = min(len(ordered) - 1, int(round(len(ordered) * 0.95)))
            p95 = ordered[idx_95]
            logger.info(
                "metrics.latency stage=%s tags=%s p50=%.1f p95=%.1f count=%s",
                stage,
                self._format_tags(tags),
                p50,
                p95,
                len(ordered),
            )
            self._last_logged[key] = now

    @staticmethod
    def _format_tags(tags: Mapping[str, str] | None) -> str:
        if not tags:
            return ""
        return ",".join(f"{key}={value}" for key, value in sorted(tags.items()))

    @staticmethod
    def _key(stage: str, tags: Mapping[str, str] | None) -> Tuple[str, Tuple[tuple[str, str], ...]]:
        tag_tuple: Tuple[tuple[str, str], ...] = tuple(sorted((tags or {}).items()))
        return stage, tag_tuple


latency_monitor = LatencyMonitor()
