from raiox.infra.circuit_breaker import BreakerState, CircuitBreaker


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_opens_after_threshold_and_closes_after_half_open_success() -> None:
    clock = FakeClock()
    cb = CircuitBreaker("x", failure_threshold=3, reset_timeout_s=10, clock=clock)

    for _ in range(2):
        assert cb.allow()
        cb.record_failure()
    assert cb.state is BreakerState.CLOSED
    cb.record_failure()
    assert cb.state is BreakerState.OPEN
    assert not cb.allow()

    clock.t = 10
    assert cb.state is BreakerState.HALF_OPEN
    assert cb.allow()  # trial request
    assert not cb.allow()  # only one at a time
    cb.record_success()
    assert cb.state is BreakerState.CLOSED


def test_failure_in_half_open_reopens() -> None:
    clock = FakeClock()
    cb = CircuitBreaker("x", failure_threshold=1, reset_timeout_s=5, clock=clock)
    cb.record_failure()
    clock.t = 5
    assert cb.allow()
    cb.record_failure()
    assert cb.state is BreakerState.OPEN
    clock.t = 9
    assert not cb.allow()


def test_success_resets_counter() -> None:
    cb = CircuitBreaker("x", failure_threshold=2)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state is BreakerState.CLOSED
