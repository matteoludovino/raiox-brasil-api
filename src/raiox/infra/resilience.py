"""Retry with exponential backoff and *full jitter* (avoids a thundering herd on sources)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable


async def call_with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int,
    should_retry: Callable[[BaseException], bool],
    base_delay_s: float = 0.2,
    max_delay_s: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:
            if attempt >= retries or not should_retry(exc):
                raise
            delay = random.uniform(0, min(max_delay_s, base_delay_s * 2**attempt))
            attempt += 1
            await sleep(delay)
