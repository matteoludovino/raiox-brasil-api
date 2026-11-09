"""Operational endpoints: liveness, readiness and metrics."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.exceptions import RedisError

router = APIRouter(tags=["Operations"])


@router.get("/health", summary="Liveness: the process is up")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Readiness: Redis reachable, plus circuit breaker states",
    responses={503: {"description": "Redis unavailable"}},
)
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    circuits = {b.name: b.state.value for b in state.breakers}
    try:
        await state.redis.ping()
        redis_ok = True
    except RedisError:
        redis_ok = False
    # Upstream sources being down does NOT pull the instance out of the load balancer:
    # it can still serve from cache.
    return JSONResponse(
        {"status": "ok" if redis_ok else "degraded", "redis": redis_ok, "circuitos": circuits},
        status_code=200 if redis_ok else 503,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
