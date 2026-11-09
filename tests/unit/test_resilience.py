import pytest

from raiox.infra.resilience import call_with_retry


async def _no_sleep(_: float) -> None:
    return None


class Transient(Exception):
    pass


async def test_retry_until_success() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise Transient
        return "ok"

    result = await call_with_retry(
        fn, retries=2, should_retry=lambda e: isinstance(e, Transient), sleep=_no_sleep
    )
    assert result == "ok"
    assert calls == 3


async def test_gives_up_after_retries() -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise Transient

    with pytest.raises(Transient):
        await call_with_retry(fn, retries=2, should_retry=lambda e: True, sleep=_no_sleep)
    assert calls == 3


async def test_does_not_retry_permanent_error() -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise ValueError

    with pytest.raises(ValueError):  # noqa: PT011
        await call_with_retry(
            fn, retries=5, should_retry=lambda e: isinstance(e, Transient), sleep=_no_sleep
        )
    assert calls == 1


async def test_backoff_respects_cap() -> None:
    delays: list[float] = []

    async def record(d: float) -> None:
        delays.append(d)

    async def fn() -> None:
        raise Transient

    with pytest.raises(Transient):
        await call_with_retry(
            fn,
            retries=6,
            should_retry=lambda e: True,
            base_delay_s=0.1,
            max_delay_s=0.5,
            sleep=record,
        )
    assert len(delays) == 6
    assert all(0 <= d <= 0.5 for d in delays)
