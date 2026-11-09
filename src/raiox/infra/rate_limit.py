"""Atomic rate limiting in Redis via Lua scripts.

* Inbound: **sliding window counter** (Cloudflare-style). Estimates usage as
  ``previous * weight + current``, with O(1) memory per key, unlike a sorted-set log,
  which is O(limit).
* Outbound: per-source **token bucket**, to honor quotas such as ReceitaWS's 3 req/min even
  with many replicas and many clients.

The clock comes from the application (injectable, which makes tests deterministic).
Trade-off: it relies on NTP across replicas. See ADR 0004.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

_SLIDING_WINDOW_LUA = """
local limit   = tonumber(ARGV[1])
local window  = tonumber(ARGV[2])
local now     = tonumber(ARGV[3])
local curr    = tonumber(redis.call('GET', KEYS[1]) or '0')
local prev    = tonumber(redis.call('GET', KEYS[2]) or '0')
local elapsed = now % window
local weight  = (window - elapsed) / window
local used    = prev * weight + curr

if used + 1 > limit then
  local retry = window - elapsed
  if curr < limit and prev > 0 then
    -- moment when the previous window's share has decayed enough
    local needed = window * (1 - (limit - 1 - curr) / prev)
    retry = math.min(retry, math.max(1, math.ceil(needed - elapsed)))
  end
  return {0, math.floor(used), retry}
end

redis.call('INCR', KEYS[1])
redis.call('PEXPIRE', KEYS[1], window * 2)
return {1, math.floor(used + 1), window - elapsed}
"""

_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])        -- tokens
local refill   = tonumber(ARGV[2])        -- milli-tokens per ms
local now      = tonumber(ARGV[3])
local state    = redis.call('HMGET', KEYS[1], 'mt', 'ts')
local mt = tonumber(state[1])             -- milli-tokens (integer: avoids floats in Redis)
local ts = tonumber(state[2])
if mt == nil then mt = capacity * 1000; ts = now end
mt = math.min(capacity * 1000, mt + math.floor((now - ts) * refill))
local allowed = 0
if mt >= 1000 then mt = mt - 1000; allowed = 1 end
redis.call('HSET', KEYS[1], 'mt', mt, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity * 1000 / refill) + 1000)
return allowed
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_s: int
    retry_after_s: int

    def headers(self) -> dict[str, str]:
        h = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.reset_s),
        }
        if not self.allowed:
            h["Retry-After"] = str(self.retry_after_s)
        return h


class SlidingWindowRateLimiter:
    def __init__(
        self,
        redis: Redis,
        *,
        window_s: int,
        prefix: str,
        clock_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._script: Any = redis.register_script(_SLIDING_WINDOW_LUA)
        self._window_ms = window_s * 1000
        self._prefix = prefix
        self._clock_ms = clock_ms

    async def hit(self, identity: str, limit: int) -> RateLimitResult:
        now = self._clock_ms()
        idx = now // self._window_ms
        # the {identity} hash tag keeps both keys in the same slot (Redis Cluster)
        base = f"{self._prefix}:rl:{{{identity}}}"
        allowed, used, ms = await self._script(
            keys=[f"{base}:{idx}", f"{base}:{idx - 1}"],
            args=[limit, self._window_ms, now],
        )
        secs = max(1, math.ceil(int(ms) / 1000))
        return RateLimitResult(
            allowed=bool(allowed),
            limit=limit,
            remaining=max(0, limit - int(used)),
            reset_s=secs,
            retry_after_s=secs,
        )


class OutboundLimiter:
    """Token bucket per external source (capacity = quota per minute)."""

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str,
        quotas_per_min: dict[str, int],
        clock_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._script: Any = redis.register_script(_TOKEN_BUCKET_LUA)
        self._prefix = prefix
        self._quotas = quotas_per_min
        self._clock_ms = clock_ms

    async def acquire(self, source: str) -> bool:
        quota = self._quotas.get(source)
        if quota is None:
            return True
        refill_per_ms = quota * 1000 / 60_000  # milli-tokens per ms
        allowed = await self._script(
            keys=[f"{self._prefix}:outbound:{source}"],
            args=[quota, refill_per_ms, self._clock_ms()],
        )
        return bool(allowed)
