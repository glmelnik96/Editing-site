"""Лимитер с фиксированным окном в памяти (один процесс API, Redis не нужен).

Фиксированное окно допускает до 2×max на стыке окон; для входа, который лишь выдаёт редирект на Яндекс,
это приемлемо, скользящее окно не нужно. Число ключей ограничено: при переполнении просроченные окна
выбрасываются, а если живых ключей всё равно больше предела, таблица сбрасывается целиком. Лучше на миг
ослабить лимит, чем дать одному клиенту раздуть память процесса.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class FixedWindowLimiter:
    def __init__(
        self,
        max_hits: int,
        window_sec: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self.max = max_hits
        self.window = window_sec
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, int]] = {}

    @property
    def tracked_keys(self) -> int:
        return len(self._buckets)

    def allow(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            if key not in self._buckets and len(self._buckets) >= self.max_keys:
                self._buckets = {k: v for k, v in self._buckets.items() if now - v[0] < self.window}
                if len(self._buckets) >= self.max_keys:
                    self._buckets.clear()
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            count += 1
            self._buckets[key] = (start, count)
            return count <= self.max
