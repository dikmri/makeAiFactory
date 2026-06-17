from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, cooldown_seconds: int) -> None:
        self._cooldown = cooldown_seconds
        self._last_job: dict[str, float] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last_job.get(key, 0.0)
        return (now - last) >= self._cooldown

    def record_job(self, key: str) -> None:
        self._last_job[key] = time.monotonic()

    def seconds_until_allowed(self, key: str) -> int:
        now = time.monotonic()
        last = self._last_job.get(key, 0.0)
        return max(0, int(self._cooldown - (now - last)))
