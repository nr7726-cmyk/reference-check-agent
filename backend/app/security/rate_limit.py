from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.sessions.store import Clock


@dataclass
class Bucket:
    tokens: float
    updated_at: datetime
    concurrent: int = 0


class RateLimitExceeded(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._buckets: dict[str, Bucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self, key: str, hourly_limit: int, concurrent_limit: int
    ) -> Callable[[], None]:
        async with self._lock:
            now = self.clock()
            bucket = self._buckets.setdefault(key, Bucket(float(hourly_limit), now))
            elapsed = max(0.0, (now - bucket.updated_at).total_seconds())
            bucket.tokens = min(
                float(hourly_limit),
                bucket.tokens + elapsed * (hourly_limit / 3600.0),
            )
            bucket.updated_at = now
            if bucket.tokens < 1 or bucket.concurrent >= concurrent_limit:
                raise RateLimitExceeded("rate limit exceeded")
            bucket.tokens -= 1
            bucket.concurrent += 1
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                bucket.concurrent = max(0, bucket.concurrent - 1)
                released = True

        return release
