"""In-process sliding-window rate limiter.

The backend runs on one server (PRD DEP-02), so per-process limits are adequate for R1.
With several Gunicorn workers each worker keeps its own window, which makes the effective
limit at most N× stricter-than-nothing; move to a shared store (DB/Redis) before scaling out.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory under abuse
                for k in list(self._hits)[:10_000]:
                    if not self._hits[k]:
                        del self._hits[k]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
