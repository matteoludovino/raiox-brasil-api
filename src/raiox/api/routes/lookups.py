"""Public lookup endpoints (require an API key and honor the rate limit).

Routes and the JSON contract stay in Portuguese; the OpenAPI docs are in English.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response

from raiox.api.deps import ContainerDep, Principal
from raiox.api.responses import build_response
from raiox.core.errors import ERROR_RESPONSES
from raiox.domain.models import CepResponse, CompanyResponse
from raiox.domain.validators import normalize_cep, normalize_cnpj

router = APIRouter(prefix="/v1")

_HEADERS_DOC: dict[str, Any] = {
    h: {"description": d, "schema": {"type": "string"}}
    for h, d in {
        "X-Cache": "HIT, MISS or STALE",
        "ETag": "Weak ETag of the data; send it back in If-None-Match",
        "RateLimit-Remaining": "Requests left in the current window",
        "X-Request-ID": "Correlates the response with the logs",
    }.items()
}
_COMMON: dict[int | str, dict[str, Any]] = {
    304: {"description": "Not modified (If-None-Match matched the ETag)"},
    **ERROR_RESPONSES,
}


@router.get(
    "/cep/{cep}",
    tags=["CEP"],
    summary="Look up a CEP by aggregating BrasilAPI, ViaCEP, OpenCEP and IBGE",
    description=(
        "Calls the sources **in parallel**, merges them field by field (with provenance) and "
        "enriches the result with IBGE municipality data. Still answers if only one source "
        "works (see `meta.avisos`)."
    ),
    response_model=CepResponse,
    responses={200: {"headers": _HEADERS_DOC}, **_COMMON},
)
async def get_cep(
    request: Request,
    c: ContainerDep,
    _: Principal,
    cep: Annotated[
        str, Path(description="8 digits, with or without hyphen", examples=["01310-100"])
    ],
) -> Response:
    result = await c.cep.lookup(normalize_cep(cep))
    return build_response(request, result, CepResponse)


@router.get(
    "/empresas/{cnpj:path}",  # :path accepts the masked CNPJ, which contains "/"
    tags=["Companies"],
    summary="Look up a CNPJ with fallback across BrasilAPI, CNPJ.ws and ReceitaWS",
    description=(
        "Sources are called **in a chain** (the next one only if the previous fails) to protect "
        "low quotas. Accepts the **alphanumeric CNPJ** (IN RFB 2.229/2024). The address is "
        "enriched through CEP aggregation (`data.localizacao`)."
    ),
    response_model=CompanyResponse,
    responses={200: {"headers": _HEADERS_DOC}, **_COMMON},
)
async def get_company(
    request: Request,
    c: ContainerDep,
    _: Principal,
    cnpj: Annotated[
        str, Path(description="14 characters, masked or not", examples=["00.000.000/0001-91"])
    ],
) -> Response:
    result = await c.companies.lookup(normalize_cnpj(cnpj))
    return build_response(request, result, CompanyResponse)
