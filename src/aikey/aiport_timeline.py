"""Fixed-size, per-minute, content-free diagnostic history (#6).

An operator who knows when a natural visit happened can read, minute by
minute, what the AI Port's motion stage and provider gate measured and
decided. Each bucket holds only a UTC minute (integer minutes since the
epoch) and integer counters or maxima from a fixed field list: no frame,
provider text, camera identity, address or path. The history keeps the last
``size`` minutes that saw activity; older buckets are dropped.
"""

from __future__ import annotations

import time
from typing import Callable

MINUTES = 180


class MinuteHistory:
    def __init__(self, counters: tuple[str, ...], maxima: tuple[str, ...] = (),
                 latest: tuple[str, ...] = (), *, size: int = MINUTES,
                 clock: Callable[[], float] = time.time):
        if not 1 <= size <= 1440 or set(counters) & set(maxima) or set(counters + maxima) & set(latest):
            raise ValueError("invalid history fields")
        self.counters, self.maxima, self.latest = counters, maxima, latest
        self.size, self.clock = size, clock
        self._buckets: list[dict[str, int | None]] = []

    def _bucket(self) -> dict[str, int | None]:
        minute = int(self.clock() // 60)
        if not self._buckets or self._buckets[-1]["minute"] != minute:
            bucket: dict[str, int | None] = {"minute": minute}
            bucket.update(dict.fromkeys(self.counters, 0))
            bucket.update(dict.fromkeys(self.maxima + self.latest))
            self._buckets.append(bucket)
            del self._buckets[:-self.size]
        return self._buckets[-1]

    def count(self, name: str, amount: int = 1) -> None:
        if name not in self.counters:
            raise KeyError(name)
        bucket = self._bucket()
        bucket[name] = min(int(bucket[name]) + amount, 1_000_000)

    def maximum(self, name: str, value: int) -> None:
        if name not in self.maxima:
            raise KeyError(name)
        bucket = self._bucket()
        current = bucket[name]
        bucket[name] = value if current is None else max(int(current), value)

    def set(self, name: str, value: int | None) -> None:
        if name not in self.latest:
            raise KeyError(name)
        self._bucket()[name] = value

    def snapshot(self) -> list[dict[str, int | None]]:
        return [dict(bucket) for bucket in self._buckets]
