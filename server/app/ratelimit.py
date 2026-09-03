"""Лимитер с фиксированным окном в памяти (один процесс API, Redis не нужен)."""
from __future__ import annotations

import time
from collections.abc import Callable


class FixedWindowLimiter:
    def __init__(self, max_hits: int, window_sec: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max = max_hits
        self.window = window_sec
        self._clock = clock
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        start, count = self._buckets.get(key, (now, 0))
        if now - start >= self.window:
            start, count = now, 0
        count += 1
        self._buckets[key] = (start, count)
        return count <= self.max
