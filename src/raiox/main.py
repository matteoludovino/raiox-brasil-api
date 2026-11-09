"""App factory and dependency wiring (composition root)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from raiox import __version__
from raiox.api.deps import Container
from raiox.api.routes import admin, lookups, ops
from raiox.core.config import Settings, get_settings
from raiox.core.errors import register_exception_handlers
from raiox.core.logging import configure_logging, get_logger
from raiox.core.middleware import RequestContextMiddleware
from raiox.infra.cache import Cache
from raiox.infra.circuit_breaker import CircuitBreaker
from raiox.infra.rate_limit import OutboundLimiter, SlidingWindowRateLimiter
from raiox.providers.base import BaseProvider
from raiox.providers.cep import BrasilApiCep, IbgeMunicipalities, OpenCep, ViaCep
from raiox.providers.cnpj import BrasilApiCnpj, CnpjWs, ReceitaWs
from raiox.security.api_keys import ApiKeyStore
from raiox.services.cep_service import CepService
from raiox.services.company_service import CompanyService

log = get_logger(__name__)

DESCRIPTION = """
Aggregator API for Brazilian public data (**CEP** postal codes and **CNPJ** company registry).

* **Aggregation**: CEP via parallel fan-out and field-by-field merge; CNPJ via a fallback chain.
* **Resilience**: timeouts, retry with jitter, circuit breakers and partial responses.
* **Cache**: Redis with negative caching, single-flight and *stale-if-error* (`X-Cache`, `ETag`).
* **Auth & limits**: API key (`X-API-Key`), per-plan rate limiting (`RateLimit-*`) and a
  per-source outbound quota.

Every response carries `meta.fontes` (who answered, status, latency) and `meta.avisos`
(degraded-mode warnings). The JSON contract uses Portuguese field names.
"""


def build_container(
    settings: Settings, redis: Redis, http: httpx.AsyncClient
) -> tuple[Container, list[CircuitBreaker]]:
    breakers: list[CircuitBreaker] = []

    def make(
        cls: type[BaseProvider],
        base_url: str,
        *,
        retries: int | None = None,
        limiter: OutboundLimiter | None = None,
        breaker_name: str | None = None,
    ) -> BaseProvider:
        breaker = CircuitBreaker(
            breaker_name or f"{cls.name}:{cls.__name__}",
            failure_threshold=settings.breaker_failure_threshold,
            reset_timeout_s=settings.breaker_reset_timeout_s,
        )
        breakers.append(breaker)
        return cls(
            http,
            base_url=base_url,
            breaker=breaker,
            timeout_s=settings.upstream_timeout_s,
            retries=settings.upstream_retries if retries is None else retries,
            retry_base_delay_s=settings.retry_base_delay_s,
            retry_max_delay_s=settings.retry_max_delay_s,
            limiter=limiter,
        )

    outbound = OutboundLimiter(
        redis,
        prefix=settings.cache_prefix,
        quotas_per_min={
            "cnpjws": settings.outbound_cnpjws_per_min,
            "receitaws": settings.outbound_receitaws_per_min,
        },
    )
    cache = Cache(
        redis,
        prefix=settings.cache_prefix,
        lock_ttl_s=settings.cache_lock_ttl_s,
        lock_wait_s=settings.cache_lock_wait_s,
    )

    cep_providers = [
        make(BrasilApiCep, settings.brasilapi_url, breaker_name="brasilapi:cep"),
        make(ViaCep, settings.viacep_url, breaker_name="viacep"),
        make(OpenCep, settings.opencep_url, breaker_name="opencep"),
    ]
    ibge = make(IbgeMunicipalities, settings.ibge_url, breaker_name="ibge")
    cep_service = CepService(cep_providers, ibge, cache, settings)  # type: ignore[arg-type]

    # Quota-limited sources get no retries: the fallback chain itself is the "retry".
    cnpj_chain = [
        make(BrasilApiCnpj, settings.brasilapi_url, breaker_name="brasilapi:cnpj"),
        make(CnpjWs, settings.cnpjws_url, retries=0, limiter=outbound, breaker_name="cnpjws"),
        make(
            ReceitaWs, settings.receitaws_url, retries=0, limiter=outbound, breaker_name="receitaws"
        ),
    ]
    company_service = CompanyService(cnpj_chain, cep_service, cache, settings)  # type: ignore[arg-type]

    container = Container(
        settings=settings,
        keys=ApiKeyStore(redis, prefix=settings.cache_prefix),
        rate_limiter=SlidingWindowRateLimiter(
            redis, window_s=settings.rate_limit_window_s, prefix=settings.cache_prefix
        ),
        cep=cep_service,
        companies=company_service,
    )
    return container, breakers


def create_app(
    settings: Settings | None = None,
    *,
    redis: Redis | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Injected resources (tests) are not closed by the app; the ones created here are."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        own_redis = redis is None
        own_http = http_client is None
        r = redis or Redis.from_url(settings.redis_url, decode_responses=True)
        http = http_client or httpx.AsyncClient(
            headers={"User-Agent": f"raiox-brasil-api/{__version__}"},
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True,
        )
        container, breakers = build_container(settings, r, http)
        app.state.container = container
        app.state.redis = r
        app.state.breakers = breakers
        log.info("startup", env=settings.env, version=__version__)
        try:
            yield
        finally:
            if own_http:
                await http.aclose()
            if own_redis:
                await r.aclose()
            log.info("shutdown")

    app = FastAPI(
        title="Raio-X Brasil API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "CEP", "description": "Address aggregated from multiple sources"},
            {"name": "Companies", "description": "CNPJ registry data with fallback"},
            {"name": "Admin", "description": "API key management (Bearer admin token)"},
            {"name": "Operations", "description": "Health, readiness and metrics"},
        ],
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(lookups.router)
    app.include_router(admin.router)
    app.include_router(ops.router)
    return app
