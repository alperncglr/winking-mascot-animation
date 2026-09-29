from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from threading import Lock


class OneTimeWebSocketTokenStore:
    def __init__(
        self,
        ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._expires_at: dict[str, float] = {}
        self._lock = Lock()

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            self._remove_expired(now)
            self._expires_at[token] = now + self.ttl_seconds
        return token

    def consume(self, token: str) -> bool:
        if not token:
            return False
        now = self.clock()
        with self._lock:
            self._remove_expired(now)
            expires_at = self._expires_at.pop(token, None)
        return expires_at is not None and expires_at >= now

    def _remove_expired(self, now: float) -> None:
        expired = [
            token
            for token, expires_at in self._expires_at.items()
            if expires_at < now
        ]
        for token in expired:
            self._expires_at.pop(token, None)
