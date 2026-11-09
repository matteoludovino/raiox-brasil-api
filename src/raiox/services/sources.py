"""Runs a call to a source and turns its outcome into a ``SourceInfo`` (for the meta block)."""

from __future__ import annotations

import time
from collections.abc import Awaitable

from raiox.domain.models import SourceInfo, SourceStatus
from raiox.providers.base import ProviderError


async def observe[T](name: str, coro: Awaitable[T]) -> tuple[T | None, SourceInfo]:
    start = time.perf_counter()
    try:
        result = await coro
    except ProviderError as exc:
        return None, SourceInfo(
            nome=name,
            status=SourceStatus(exc.outcome),
            latencia_ms=ms_since(start),
            detalhe=str(exc),
        )
    return result, SourceInfo(nome=name, status=SourceStatus.OK, latencia_ms=ms_since(start))


def ms_since(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
