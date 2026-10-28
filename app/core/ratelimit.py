"""A small in-process rate limiter for unauthenticated endpoints.

Sliding window per (bucket, key). Per-process only: with several API
workers each one counts separately, which still caps abuse at a small
multiple of the limit. Swap for a shared store (Redis) if the public
endpoints ever need exact limits across workers.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from app.core.errors import AppError

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque] = {}
_MAX_KEYS = 50_000


def check(bucket: str, key: str | None, limit: int, window_seconds: int = 60) -> None:
    """Raise 429 when ``key`` has made ``limit`` calls in the last window."""
    now = time.monotonic()
    k = (bucket, key or "-")
    with _lock:
        if len(_hits) > _MAX_KEYS:  # bound memory: drop idle keys
            for kk in [kk for kk, d in _hits.items() if not d or now - d[-1] > window_seconds]:
                _hits.pop(kk, None)
        d = _hits.setdefault(k, deque())
        while d and now - d[0] > window_seconds:
            d.popleft()
        if len(d) >= limit:
            raise AppError("rate_limited", "Too many requests — please wait a minute and try again.", 429)
        d.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()
