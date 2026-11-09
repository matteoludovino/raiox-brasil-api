"""Builds the HTTP response for a lookup: data+meta envelope, ETag/304 and cache headers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from raiox.core.logging import request_id_ctx
from raiox.domain.models import Aggregate, CacheStatus, Meta
from raiox.infra.cache import CachedValue

# Returned in meta.avisos, so it stays in Portuguese.
STALE_WARNING = "Fontes indisponíveis: servindo última cópia conhecida (stale-if-error)."


def weak_etag(data: BaseModel) -> str:
    # Weak ETag: the body includes meta (request_id, cache) that changes on every call,
    # but the representation of the *data* is semantically the same.
    canonical = json.dumps(data.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f'W/"{hashlib.sha256(canonical.encode()).hexdigest()[:20]}"'


def _matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    bare = etag.removeprefix("W/")
    return any(tag.strip().removeprefix("W/") == bare for tag in if_none_match.split(","))


def build_response(
    request: Request,
    cached: CachedValue[Aggregate[Any]],
    envelope: type[BaseModel],
) -> Response:
    agg = cached.value
    etag = weak_etag(agg.data)
    headers = {
        **getattr(request.state, "rate_limit_headers", {}),
        "ETag": etag,
        "Cache-Control": f"private, max-age={cached.max_age()}",
        "X-Cache": cached.status.value,
        "Vary": "X-API-Key",
    }
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    warnings = list(agg.avisos)
    if cached.status is CacheStatus.STALE:
        warnings.insert(0, STALE_WARNING)
    meta = Meta(
        request_id=request_id_ctx.get(),
        cache=cached.status,
        obtido_em=agg.obtido_em,
        fontes=agg.fontes,
        avisos=warnings,
    )
    body = envelope.model_validate({"data": agg.data, "meta": meta})
    return JSONResponse(body.model_dump(mode="json"), headers=headers)
