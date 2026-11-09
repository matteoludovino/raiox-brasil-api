"""Pure ASGI middleware: request id, access log and HTTP metrics."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from raiox.core.logging import get_logger, request_id_ctx
from raiox.infra.metrics import HTTP_LATENCY, HTTP_REQUESTS

log = get_logger("raiox.access")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Accepts the client X-Request-ID (if safe) or generates one; echoes it back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            # Use the route template (/v1/cep/{cep}) to keep metric cardinality bounded.
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_LATENCY.labels(method, route).observe(elapsed)
            if route not in ("/health", "/metrics"):
                log.info(
                    "http_request",
                    method=method,
                    route=route,
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round(elapsed * 1000, 1),
                )
            structlog.contextvars.unbind_contextvars("request_id")
            request_id_ctx.reset(token)
