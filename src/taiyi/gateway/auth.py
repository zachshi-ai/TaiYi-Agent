"""Gateway auth and rate limiting — small, explicit, fail-closed where it matters.

Token auth is *opt-in*: with no tokens configured the gateway is open (convenient
for local/CLI use); configure tokens and every request must present a valid
Bearer token. The rate limiter is a simple per-identity sliding window.
"""
from __future__ import annotations

import time


class AuthPolicy:
    def __init__(self, tokens: tuple[str, ...] = ()):
        self.tokens = set(tokens)

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)

    def authorize(self, headers) -> bool:
        if not self.enabled:
            return True
        value = headers.get("Authorization") or headers.get("authorization") or ""
        return value.startswith("Bearer ") and value[7:] in self.tokens


class RateLimiter:
    def __init__(self, max_per_window: int = 120, window: float = 60.0):
        self.max = max_per_window
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.max:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
