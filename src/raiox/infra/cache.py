"""Cache-aside on Redis with negative caching, single-flight and stale-if-error.

Each entry is **one** key holding an envelope ``{"fresh_until", "nf", "data"}``:

* Physical TTL = ttl + stale_ttl. After ``fresh_until`` the entry is *stale*: it is not served
  normally, but it is the safety net if every source fails.
* ``nf`` = negative cache (404), so non-existent CEPs/CNPJs don't hammer the sources.
* Single-flight: a ``SET NX`` lock ensures only one request (across all replicas) goes to the
  sources per key. The others wait for the value to appear (stampede protection).

Redis failures do **not** break the lookup: the cache is treated as an optimization (fail-open).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

from raiox.core.errors import NotFoundError, UpstreamUnavailableError
from raiox.core.logging import get_logger
from raiox.domain.models import CacheStatus
from raiox.infra.metrics import CACHE_EVENTS

log = get_logger(__name__)

_RELEASE_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


@dataclass(frozen=True, slots=True)
class CachedValue[M: BaseModel]:
    value: M
    status: CacheStatus
    fresh_until: float

    def max_age(self, now: float | None = None) -> int:
        if self.status is CacheStatus.STALE:
            return 0
        return max(0, int(self.fresh_until - (now or time.time())))


@dataclass(frozen=True, slots=True)
class _Entry:
    fresh_until: float
    not_found: bool
    detail: str | None
    data: Any


class Cache:
    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str,
        lock_ttl_s: float = 10.0,
        lock_wait_s: float = 7.0,
        poll_interval_s: float = 0.05,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._lock_ttl_ms = int(lock_ttl_s * 1000)
        self._lock_wait_s = lock_wait_s
        self._poll_s = poll_interval_s
        self._clock = clock
        self._release: Any = redis.register_script(_RELEASE_LOCK_LUA)

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def get_or_load[M: BaseModel](
        self,
        key: str,
        model: type[M],
        loader: Callable[[], Awaitable[M]],
        *,
        ttl_s: int | Callable[[M], int],
        negative_ttl_s: int,
        stale_ttl_s: int,
    ) -> CachedValue[M]:
        resource = key.split(":", 1)[0]
        stale: _Entry | None = None

        entry = await self._read(key)
        if entry and self._is_fresh(entry):
            return self._hit(entry, model, resource)
        stale = entry if entry and not entry.not_found else None

        lock_token = await self._acquire_lock(key)
        try:
            if lock_token is None:
                # Someone else is loading: wait for their result instead of duplicating work.
                entry = await self._wait_for_fresh(key)
                if entry:
                    return self._hit(entry, model, resource)
                lock_token = await self._acquire_lock(key)  # still empty: try to take over

            try:
                value = await loader()
            except NotFoundError as exc:
                await self._write(
                    key,
                    _Entry(self._clock() + negative_ttl_s, True, exc.detail, None),
                    negative_ttl_s,
                )
                CACHE_EVENTS.labels(resource, "negative_store").inc()
                raise
            except UpstreamUnavailableError:
                if stale is not None:
                    CACHE_EVENTS.labels(resource, "stale").inc()
                    log.warning("cache_stale_served", key=key)
                    return CachedValue(
                        model.model_validate(stale.data), CacheStatus.STALE, stale.fresh_until
                    )
                raise

            ttl = ttl_s(value) if callable(ttl_s) else ttl_s  # e.g. shorter when degraded
            fresh_until = self._clock() + ttl
            await self._write(
                key,
                _Entry(fresh_until, False, None, value.model_dump(mode="json")),
                ttl + stale_ttl_s,
            )
            CACHE_EVENTS.labels(resource, "miss").inc()
            return CachedValue(value, CacheStatus.MISS, fresh_until)
        finally:
            if lock_token is not None:
                await self._release_lock(key, lock_token)

    # --- helpers ------------------------------------------------------------------
    def _is_fresh(self, entry: _Entry) -> bool:
        return entry.fresh_until > self._clock()

    def _hit[M: BaseModel](self, entry: _Entry, model: type[M], resource: str) -> CachedValue[M]:
        if entry.not_found:
            CACHE_EVENTS.labels(resource, "negative_hit").inc()
            raise NotFoundError(entry.detail)
        CACHE_EVENTS.labels(resource, "hit").inc()
        return CachedValue(model.model_validate(entry.data), CacheStatus.HIT, entry.fresh_until)

    async def _read(self, key: str) -> _Entry | None:
        try:
            raw = await self._redis.get(self._key(key))
        except RedisError as exc:
            log.warning("cache_unavailable", op="get", error=str(exc))
            return None
        if raw is None:
            return None
        try:
            d = json.loads(raw)
            return _Entry(float(d["fresh_until"]), bool(d["nf"]), d.get("detail"), d.get("data"))
        except (ValueError, KeyError, TypeError):
            log.warning("cache_entry_corrupted", key=key)
            return None

    async def _write(self, key: str, entry: _Entry, ttl_s: int) -> None:
        payload = json.dumps(
            {
                "fresh_until": entry.fresh_until,
                "nf": entry.not_found,
                "detail": entry.detail,
                "data": entry.data,
            },
            ensure_ascii=False,
        )
        try:
            await self._redis.set(self._key(key), payload, ex=max(1, ttl_s))
        except RedisError as exc:
            log.warning("cache_unavailable", op="set", error=str(exc))

    async def _acquire_lock(self, key: str) -> str | None:
        token = uuid.uuid4().hex
        try:
            ok = await self._redis.set(
                self._key(f"lock:{key}"), token, nx=True, px=self._lock_ttl_ms
            )
        except RedisError:
            return "no-redis"  # without Redis there is no coordination: just load
        return token if ok else None

    async def _release_lock(self, key: str, token: str) -> None:
        with contextlib.suppress(RedisError):
            await self._release(keys=[self._key(f"lock:{key}")], args=[token])

    async def _wait_for_fresh(self, key: str) -> _Entry | None:
        deadline = self._clock() + self._lock_wait_s
        while self._clock() < deadline:
            await asyncio.sleep(self._poll_s)
            entry = await self._read(key)
            if entry and self._is_fresh(entry):
                return entry
            try:
                if not await self._redis.exists(self._key(f"lock:{key}")):
                    return None  # the lock owner finished without writing (e.g. sources down)
            except RedisError:
                return None
        return None
