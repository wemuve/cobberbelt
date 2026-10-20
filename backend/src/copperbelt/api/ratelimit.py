"""Fixed-window rate limiting.

In-process only: correct for a single API instance. With several instances behind a
load balancer, move the counters to Redis or enforce limits at the gateway
(documented in docs/operations.md). Limits are per client IP and per route group.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from copperbelt.errors import TooManyRequests


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> None:
        now = int(time.time()) // window_seconds
        with self._lock:
            window, count = self._windows[key]
            if window != now:
                window, count = now, 0
            count += 1
            self._windows[key] = (window, count)
            if len(self._windows) > 100_000:  # bound memory under abuse
                self._windows.clear()
        if count > limit:
            raise TooManyRequests("Too many requests; slow down and retry shortly")

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


limiter = RateLimiter()
