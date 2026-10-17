"""Abuse limits for a public deployment.

Uploads need no account, so each visitor (by client address) and the site as
a whole have limits. Counts live in memory, which is enough because the API
runs as a single process; a restart only forgets recent counts.
"""

import os
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

from fastapi import Request

from .config import Settings


class RateLimiter:
    """Sliding-window counters: at most ``limit`` events per ``window`` seconds per key."""

    def __init__(self) -> None:
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            if len(self._events) > 50_000:  # forget idle visitors
                for stale in [k for k, v in self._events.items() if not v]:
                    del self._events[stale]
            return True


def client_address(request: Request, settings: Settings) -> str:
    """The visitor's address. Behind Cloudflare, set IR_CLIENT_IP_HEADER=cf-connecting-ip.

    The header is trusted only when configured: anyone can send it, so it is
    meaningful only when the proxy in front replaces it.
    """
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header, "").split(",")[0].strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


class StorageMeter:
    """Total size of stored books, measured at most once a minute."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._measured = -float("inf")
        self._bytes = 0

    def megabytes(self) -> float:
        if time.monotonic() - self._measured > 60:
            total = 0
            for folder, _, files in os.walk(self._root):
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(folder, name))
                    except OSError:
                        pass  # deleted while walking
            self._bytes, self._measured = total, time.monotonic()
        return self._bytes / (1024 * 1024)
