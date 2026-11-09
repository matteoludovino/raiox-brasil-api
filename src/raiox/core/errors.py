"""Application errors and RFC 9457 problem responses (application/problem+json).

Titles and details are returned to API clients, so they stay in Portuguese.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from raiox.core.logging import get_logger, request_id_ctx

log = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"
_TYPE_BASE = "https://raiox.dev/problemas/"


class ProblemDetail(BaseModel):
    """Standard error body (RFC 9457)."""

    type: str = Field(examples=[f"{_TYPE_BASE}nao-encontrado"])
    title: str = Field(examples=["Recurso não encontrado"])
    status: int = Field(examples=[404])
    detail: str | None = Field(default=None, examples=["CEP 99999-999 não encontrado"])
    instance: str | None = Field(default=None, examples=["/v1/cep/99999999"])
    request_id: str | None = None
    errors: list[dict[str, Any]] | None = None


class AppError(Exception):
    status: int = 500
    title: str = "Erro interno"
    slug: str = "erro-interno"

    def __init__(self, detail: str | None = None, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.headers = headers or {}


class InvalidInputError(AppError):
    status, title, slug = 422, "Entrada inválida", "entrada-invalida"


class NotFoundError(AppError):
    status, title, slug = 404, "Recurso não encontrado", "nao-encontrado"


class UnauthorizedError(AppError):
    status, title, slug = 401, "Não autenticado", "nao-autenticado"


class RateLimitedError(AppError):
    status, title, slug = 429, "Limite de requisições excedido", "limite-excedido"


class UpstreamUnavailableError(AppError):
    status, title, slug = 503, "Fontes indisponíveis", "fontes-indisponiveis"


class DependencyUnavailableError(AppError):
    status, title, slug = 503, "Dependência indisponível", "dependencia-indisponivel"


def _problem(
    request: Request,
    *,
    status: int,
    title: str,
    slug: str,
    detail: str | None,
    headers: dict[str, str] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body = ProblemDetail(
        type=f"{_TYPE_BASE}{slug}",
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        request_id=request_id_ctx.get(),
        errors=errors,
    )
    merged = {**getattr(request.state, "rate_limit_headers", {}), **(headers or {})}
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        headers=merged,
        media_type=PROBLEM_JSON,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return _problem(
            request,
            status=exc.status,
            title=exc.title,
            slug=exc.slug,
            detail=exc.detail,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return _problem(
            request,
            status=422,
            title=InvalidInputError.title,
            slug=InvalidInputError.slug,
            detail="A requisição não passou na validação",
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(
            request,
            status=exc.status_code,
            title=str(exc.detail) if exc.status_code < 500 else "Erro interno",
            slug=f"http-{exc.status_code}",
            detail=None,
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return _problem(
            request,
            status=500,
            title=AppError.title,
            slug=AppError.slug,
            detail="Erro inesperado. Informe o request_id ao suporte.",
        )


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ProblemDetail, "description": desc, "content": {PROBLEM_JSON: {}}}
    for code, desc in {
        401: "Missing, invalid or revoked API key",
        404: "Resource not found in any source",
        422: "Invalid input (format or check digit)",
        429: "Plan rate limit exceeded",
        503: "Every source is unavailable and there is no cached copy",
    }.items()
}
