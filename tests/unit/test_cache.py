import asyncio

import pytest
from fakeredis import FakeAsyncRedis

from raiox.core.errors import NotFoundError, UpstreamUnavailableError
from raiox.domain.models import CacheStatus, Municipality
from raiox.infra.cache import Cache

M = Municipality(codigo_ibge="1", nome="X")


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


def make(redis: FakeAsyncRedis, clock: Clock | None = None) -> Cache:
    return Cache(redis, prefix="t", lock_wait_s=1, poll_interval_s=0.01, clock=clock or Clock())


KW = {"ttl_s": 60, "negative_ttl_s": 30, "stale_ttl_s": 600}


async def test_miss_then_hit(redis: FakeAsyncRedis) -> None:
    cache = make(redis)
    calls = 0

    async def loader() -> Municipality:
        nonlocal calls
        calls += 1
        return M

    first = await cache.get_or_load("ibge:1", Municipality, loader, **KW)
    second = await cache.get_or_load("ibge:1", Municipality, loader, **KW)
    assert (first.status, second.status) == (CacheStatus.MISS, CacheStatus.HIT)
    assert second.value == M
    assert calls == 1


async def test_negative_cache(redis: FakeAsyncRedis) -> None:
    cache = make(redis)
    calls = 0

    async def loader() -> Municipality:
        nonlocal calls
        calls += 1
        raise NotFoundError("não existe")

    for _ in range(3):
        with pytest.raises(NotFoundError, match="não existe"):
            await cache.get_or_load("ibge:404", Municipality, loader, **KW)
    assert calls == 1
    assert 0 < await redis.ttl("t:ibge:404") <= 30


async def test_stale_if_error(redis: FakeAsyncRedis) -> None:
    clock = Clock()
    cache = make(redis, clock)

    async def ok() -> Municipality:
        return M

    async def down() -> Municipality:
        raise UpstreamUnavailableError("fora")

    await cache.get_or_load("ibge:1", Municipality, ok, **KW)
    clock.t += 61  # no longer fresh, but still inside the stale window
    stale = await cache.get_or_load("ibge:1", Municipality, down, **KW)
    assert stale.status is CacheStatus.STALE
    assert stale.value == M
    assert stale.max_age() == 0

    with pytest.raises(UpstreamUnavailableError):
        await cache.get_or_load("ibge:2", Municipality, down, **KW)


async def test_expired_entry_is_reloaded(redis: FakeAsyncRedis) -> None:
    clock = Clock()
    cache = make(redis, clock)

    async def ok() -> Municipality:
        return M

    await cache.get_or_load("ibge:1", Municipality, ok, **KW)
    clock.t += 61
    again = await cache.get_or_load("ibge:1", Municipality, ok, **KW)
    assert again.status is CacheStatus.MISS


async def test_single_flight_prevents_stampede(redis: FakeAsyncRedis) -> None:
    cache = make(redis)
    calls = 0

    async def slow() -> Municipality:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return M

    results = await asyncio.gather(
        *(cache.get_or_load("ibge:1", Municipality, slow, **KW) for _ in range(10))
    )
    assert calls == 1
    assert sorted(r.status for r in results).count(CacheStatus.MISS) == 1
    assert all(r.value == M for r in results)


async def test_redis_down_does_not_break_lookup() -> None:
    broken = FakeAsyncRedis(decode_responses=True, connected=False)
    cache = make(broken)

    async def ok() -> Municipality:
        return M

    result = await cache.get_or_load("ibge:1", Municipality, ok, **KW)
    assert result.status is CacheStatus.MISS
    assert result.value == M
