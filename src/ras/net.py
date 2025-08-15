# ─────────────────────────────────────────────────────────────────────────────
# File: ras/net.py
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import asyncio
from typing import Callable, Any
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type


class RateLimiter:
    """Simple async semaphore-based rate limiter."""

    def __init__(self, max_concurrent: int):
        self._sem = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self):
        await self._sem.acquire()

    async def __aexit__(self, exc_type, exc, tb):
        self._sem.release()


def async_retryable(max_attempts: int = 5):
    """Decorator for async retries with exponential jitter backoff."""

    def deco(fn: Callable[..., Any]):
        @retry(
            reraise=True,
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=1, max=10),
            retry=retry_if_exception_type((Exception,)),
        )
        async def wrapper(*args, **kwargs):
            return await fn(*args, **kwargs)

        return wrapper

    return deco
    