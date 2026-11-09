"""Base class for external source adapters.

Each source is an adapter that (1) knows the source URL and payload format and (2) normalizes
it into the domain model. Timeout, retry, circuit breaker, outbound quota and metrics live here,
in one place, instead of being scattered across adapters.

Exception messages end up in ``meta.fontes[].detalhe`` of API responses (Portuguese).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from raiox.core.logging import get_logger
from raiox.infra.circuit_breaker import BreakerState, CircuitBreaker
from raiox.infra.metrics import UPSTREAM_LATENCY, UPSTREAM_REQUESTS
from raiox.infra.rate_limit import OutboundLimiter
from raiox.infra.resilience import call_with_retry

log = get_logger(__name__)


# --- Source errors (mapped to SourceStatus by the services) --------------------
class ProviderError(Exception):
    outcome = "erro"


class ProviderNotFound(ProviderError):
    outcome = "nao_encontrado"


class ProviderRejected(ProviderError):
    """Any 4xx other than 404: the source refused the input (e.g. unsupported alphanumeric CNPJ)."""

    outcome = "rejeitado"


class ProviderUnavailable(ProviderError):
    """Transient failure (5xx, network): eligible for retry and counted by the circuit breaker."""

    outcome = "erro"


class ProviderTimeout(ProviderUnavailable):
    outcome = "timeout"


class ProviderBadResponse(ProviderError):
    outcome = "erro"


class CircuitOpen(ProviderError):
    outcome = "circuito_aberto"


class QuotaExceeded(ProviderError):
    outcome = "cota_esgotada"


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, ProviderUnavailable)


class BaseProvider:
    name: str = "base"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        breaker: CircuitBreaker,
        timeout_s: float,
        retries: int,
        retry_base_delay_s: float = 0.2,
        retry_max_delay_s: float = 2.0,
        limiter: OutboundLimiter | None = None,
    ) -> None:
        self._client = client
        self.base_url = base_url.rstrip("/")
        self.breaker = breaker
        self._timeout = timeout_s
        self._retries = retries
        self._base_delay = retry_base_delay_s
        self._max_delay = retry_max_delay_s
        self._limiter = limiter

    async def _get_json(self, path: str) -> Any:
        if self.breaker.state is BreakerState.OPEN:
            UPSTREAM_REQUESTS.labels(self.name, "circuit_open").inc()
            raise CircuitOpen(f"{self.name}: circuito aberto")
        # The local quota is checked once per call (not per retry) and does NOT count as a
        # source failure: we exhausted it, the source did nothing wrong.
        if self._limiter is not None and not await self._limiter.acquire(self.name):
            UPSTREAM_REQUESTS.labels(self.name, "quota_exceeded").inc()
            raise QuotaExceeded(f"{self.name}: cota de saída esgotada")
        if not self.breaker.allow():  # half-open with the trial request already in flight
            raise CircuitOpen(f"{self.name}: circuito em teste")
        try:
            data = await call_with_retry(
                lambda: self._request(path),
                retries=self._retries,
                should_retry=_retryable,
                base_delay_s=self._base_delay,
                max_delay_s=self._max_delay,
            )
        except (ProviderUnavailable, ProviderBadResponse, QuotaExceeded):
            self.breaker.record_failure()
            raise
        except (ProviderNotFound, ProviderRejected):
            self.breaker.record_success()  # the source answered: it is healthy
            raise
        self.breaker.record_success()
        return data

    async def _request(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        start = time.perf_counter()
        try:
            resp = await self._client.get(url, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            self._observe("timeout", start)
            raise ProviderTimeout(f"{self.name}: timeout") from exc
        except httpx.HTTPError as exc:
            self._observe("network_error", start)
            raise ProviderUnavailable(f"{self.name}: {type(exc).__name__}") from exc

        self._observe(str(resp.status_code), start)
        if resp.status_code == 404:
            raise ProviderNotFound(f"{self.name}: não encontrado")
        if resp.status_code == 429:
            raise QuotaExceeded(f"{self.name}: limite da fonte (429)")
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"{self.name}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderRejected(f"{self.name}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderBadResponse(f"{self.name}: JSON inválido") from exc

    def _observe(self, outcome: str, start: float) -> None:
        elapsed = time.perf_counter() - start
        UPSTREAM_REQUESTS.labels(self.name, outcome).inc()
        UPSTREAM_LATENCY.labels(self.name).observe(elapsed)
        log.debug("upstream", source=self.name, outcome=outcome, ms=round(elapsed * 1000, 1))
