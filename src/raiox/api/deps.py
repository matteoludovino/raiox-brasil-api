"""FastAPI dependencies: container, authentication and rate limiting.

Error messages are returned to API clients, so they stay in Portuguese.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError

from raiox.core.config import Settings
from raiox.core.errors import DependencyUnavailableError, RateLimitedError, UnauthorizedError
from raiox.core.logging import get_logger
from raiox.infra.metrics import RATE_LIMITED
from raiox.infra.rate_limit import SlidingWindowRateLimiter
from raiox.security.api_keys import ApiKeyInfo, ApiKeyStore
from raiox.services.cep_service import CepService
from raiox.services.company_service import CompanyService

log = get_logger(__name__)


@dataclass(slots=True)
class Container:
    settings: Settings
    keys: ApiKeyStore
    rate_limiter: SlidingWindowRateLimiter
    cep: CepService
    companies: CompanyService


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]

_api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="Create a key with POST /admin/api-keys"
)
_admin_bearer = HTTPBearer(auto_error=False, description="RAIOX_ADMIN_TOKEN")


async def require_api_key(
    c: ContainerDep, raw: Annotated[str | None, Depends(_api_key_header)]
) -> ApiKeyInfo:
    if not raw:
        raise UnauthorizedError(
            "Envie a API key no header X-API-Key", headers={"WWW-Authenticate": "ApiKey"}
        )
    try:
        info = await c.keys.verify(raw)
    except RedisError as exc:
        # Without Redis we cannot validate the key: fail closed (security over availability).
        raise DependencyUnavailableError("Armazenamento de credenciais indisponível") from exc
    if info is None:
        raise UnauthorizedError(
            "API key inválida ou revogada", headers={"WWW-Authenticate": "ApiKey"}
        )
    return info


async def enforce_rate_limit(
    request: Request,
    c: ContainerDep,
    principal: Annotated[ApiKeyInfo, Depends(require_api_key)],
) -> ApiKeyInfo:
    limit = c.settings.rate_limits[principal.plan]
    try:
        result = await c.rate_limiter.hit(principal.id, limit)
    except RedisError as exc:
        # Rate limiting fails open: better to serve without limits than to take the API down.
        log.warning("rate_limit_unavailable", error=str(exc))
        return principal
    request.state.rate_limit_headers = result.headers()
    if not result.allowed:
        RATE_LIMITED.labels(principal.plan).inc()
        raise RateLimitedError(
            f"Plano '{principal.plan}' permite {limit} requisições por "
            f"{c.settings.rate_limit_window_s}s. Tente novamente em {result.retry_after_s}s.",
            headers=result.headers(),
        )
    return principal


async def require_admin(
    c: ContainerDep, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_admin_bearer)]
) -> None:
    expected = c.settings.admin_token.get_secret_value().encode()
    if creds is None or not secrets.compare_digest(creds.credentials.encode(), expected):
        raise UnauthorizedError(
            "Token de administrador inválido", headers={"WWW-Authenticate": "Bearer"}
        )


Principal = Annotated[ApiKeyInfo, Depends(enforce_rate_limit)]
