"""Optional API key + light rate limiting for Hidden Clearances integration (M4)."""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import Header, HTTPException, Request


def configured_api_key() -> str:
    return (os.environ.get("API_KEY") or os.environ.get("WALMART_API_KEY") or "").strip()


def require_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
) -> None:
    """
    If API_KEY is set in the environment, require it on protected routes.
    Accepts X-API-Key: <key> or Authorization: Bearer <key>.
    """
    expected = configured_api_key()
    if not expected:
        return
    token = (x_api_key or "").strip()
    if not token and authorization:
        raw = authorization.strip()
        if raw.lower().startswith("bearer "):
            token = raw[7:].strip()
        else:
            token = raw
    if not token or token != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Send X-API-Key or Authorization: Bearer <key>.",
        )


class RateLimiter:
    """In-memory sliding window (per process). Enough for a single Railway replica."""

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def limit_per_minute(self) -> int:
        raw = os.environ.get("RATE_LIMIT_PER_MIN", "60").strip()
        try:
            return max(0, int(raw))
        except ValueError:
            return 60

    def check(self, key: str) -> tuple[bool, int, int]:
        """
        Returns (allowed, remaining, limit).
        limit=0 means rate limiting disabled.
        """
        limit = self.limit_per_minute()
        if limit <= 0:
            return True, -1, 0
        now = time.time()
        window = self._hits[key]
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= limit:
            return False, 0, limit
        window.append(now)
        return True, max(0, limit - len(window)), limit


rate_limiter = RateLimiter()


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def enforce_rate_limit(request: Request) -> Dict[str, str]:
    allowed, remaining, limit = rate_limiter.check(client_key(request))
    headers = {}
    if limit > 0:
        headers["X-RateLimit-Limit"] = str(limit)
        headers["X-RateLimit-Remaining"] = str(max(0, remaining))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({limit} requests/minute). Retry shortly.",
            headers=headers or None,
        )
    return headers
