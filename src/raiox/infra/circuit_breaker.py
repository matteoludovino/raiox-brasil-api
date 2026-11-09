"""Per-source circuit breaker (in-process).

closed --(N consecutive failures)--> open --(reset_timeout)--> half_open --(1 success)--> closed
                                                                 \\--(failure)--> open

Trade-off (ADR 0002): state is not shared across replicas. Each replica learns on its own
that a source is down, which costs at most N extra calls per replica.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

    @property
    def state(self) -> BreakerState:
        if (
            self._state is BreakerState.OPEN
            and self._clock() - self._opened_at >= self.reset_timeout_s
        ):
            self._state = BreakerState.HALF_OPEN
            self._trial_in_flight = False
        return self._state

    def allow(self) -> bool:
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.HALF_OPEN and not self._trial_in_flight:
            self._trial_in_flight = True  # only one trial request at a time
            return True
        return False

    def record_success(self) -> None:
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._trial_in_flight = False

    def record_failure(self) -> None:
        if self._state is BreakerState.HALF_OPEN:
            self._open()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._trial_in_flight = False
