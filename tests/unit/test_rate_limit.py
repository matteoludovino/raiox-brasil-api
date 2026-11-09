from fakeredis import FakeAsyncRedis

from raiox.infra.rate_limit import OutboundLimiter, SlidingWindowRateLimiter


class Clock:
    def __init__(self, t: int) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


async def test_sliding_window_blocks_after_limit(redis: FakeAsyncRedis) -> None:
    clock = Clock(60_000 * 100)  # exact start of a window
    rl = SlidingWindowRateLimiter(redis, window_s=60, prefix="t", clock_ms=clock)

    results = [await rl.hit("k1", 3) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[2].remaining == 0
    blocked = results[3]
    assert blocked.retry_after_s > 0
    assert blocked.headers()["Retry-After"] == str(blocked.retry_after_s)

    # another identity has its own counter
    assert (await rl.hit("k2", 3)).allowed


async def test_sliding_window_weights_previous_window(redis: FakeAsyncRedis) -> None:
    clock = Clock(60_000 * 100)
    rl = SlidingWindowRateLimiter(redis, window_s=60, prefix="t", clock_ms=clock)
    for _ in range(4):
        assert (await rl.hit("k", 4)).allowed

    # 15s into the next window: previous weight = 75% -> 3 used -> 1 left
    clock.t += 75_000
    assert (await rl.hit("k", 4)).allowed
    blocked = await rl.hit("k", 4)
    assert not blocked.allowed
    assert blocked.retry_after_s <= 45

    # 45s later the previous window weighs 0
    clock.t += 45_000
    assert (await rl.hit("k", 4)).allowed


async def test_outbound_token_bucket(redis: FakeAsyncRedis) -> None:
    clock = Clock(1_000_000)
    lim = OutboundLimiter(redis, prefix="t", quotas_per_min={"receitaws": 3}, clock_ms=clock)

    assert [await lim.acquire("receitaws") for _ in range(4)] == [True, True, True, False]
    clock.t += 20_000  # 3/min => 1 token every 20s
    assert await lim.acquire("receitaws")
    assert not await lim.acquire("receitaws")
    assert await lim.acquire("fonte-sem-cota")
