from decimal import Decimal

import httpx
import pytest
import respx

from raiox.infra.circuit_breaker import BreakerState, CircuitBreaker
from raiox.providers.base import (
    CircuitOpen,
    ProviderNotFound,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    QuotaExceeded,
)
from raiox.providers.cep import BrasilApiCep, IbgeMunicipalities, OpenCep, ViaCep
from raiox.providers.cnpj import BrasilApiCnpj, CnpjWs, ReceitaWs
from tests import payloads as p
from tests.conftest import URLS


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


def build(cls, http, base, *, retries=1, threshold=5):
    return cls(
        http,
        base_url=base,
        breaker=CircuitBreaker(cls.name, failure_threshold=threshold),
        timeout_s=1,
        retries=retries,
        retry_base_delay_s=0,
        retry_max_delay_s=0,
    )


async def test_brasilapi_cep_normalizes(http, upstream: respx.MockRouter) -> None:
    upstream.get(f"{URLS['brasilapi']}/cep/v2/{p.CEP}").respond(json=p.BRASILAPI_CEP)
    r = await build(BrasilApiCep, http, URLS["brasilapi"]).fetch(p.CEP)
    assert r.cidade == "São Paulo"
    assert r.coordenadas is not None
    assert r.coordenadas.latitude == pytest.approx(-23.5629)


async def test_brasilapi_cep_without_coordinates(http, upstream: respx.MockRouter) -> None:
    upstream.get(f"{URLS['brasilapi']}/cep/v2/{p.CEP}").respond(
        json={**p.BRASILAPI_CEP, "location": {"type": "Point", "coordinates": {}}}
    )
    r = await build(BrasilApiCep, http, URLS["brasilapi"]).fetch(p.CEP)
    assert r.coordenadas is None


async def test_viacep_erro_true_becomes_not_found(http, upstream: respx.MockRouter) -> None:
    upstream.get(f"{URLS['viacep']}/99999999/json/").respond(json={"erro": "true"})
    with pytest.raises(ProviderNotFound):
        await build(ViaCep, http, URLS["viacep"]).fetch("99999999")


async def test_opencep_and_ibge(http, upstream: respx.MockRouter) -> None:
    upstream.get(f"{URLS['opencep']}/{p.CEP}").respond(json=p.OPENCEP)
    upstream.get(f"{URLS['ibge']}/localidades/municipios/{p.IBGE_SP}").respond(
        json=p.IBGE_MUNICIPIO
    )
    upstream.get(f"{URLS['ibge']}/localidades/municipios/1").respond(json=[])

    r = await build(OpenCep, http, URLS["opencep"]).fetch(p.CEP)
    assert r.codigo_ibge == p.IBGE_SP
    ibge = build(IbgeMunicipalities, http, URLS["ibge"])
    m = await ibge.get_municipality(p.IBGE_SP)
    assert (m.uf, m.regiao, m.mesorregiao) == ("SP", "Sudeste", "Metropolitana de São Paulo")
    with pytest.raises(ProviderNotFound):
        await ibge.get_municipality("1")


async def test_ibge_without_microregion_uses_immediate_region(
    http, upstream: respx.MockRouter
) -> None:
    payload = {**p.IBGE_MUNICIPIO, "microrregiao": None}
    upstream.get(f"{URLS['ibge']}/localidades/municipios/5101837").respond(json=payload)
    m = await build(IbgeMunicipalities, http, URLS["ibge"]).get_municipality("5101837")
    assert m.uf == "SP"
    assert m.microrregiao is None


@pytest.mark.parametrize(
    ("cls", "base", "path", "payload"),
    [
        (BrasilApiCnpj, "brasilapi", f"/cnpj/v1/{p.CNPJ}", p.BRASILAPI_CNPJ),
        (CnpjWs, "cnpjws", f"/cnpj/{p.CNPJ}", p.CNPJWS),
        (ReceitaWs, "receitaws", f"/cnpj/{p.CNPJ}", p.RECEITAWS),
    ],
)
async def test_cnpj_sources_converge_to_same_model(
    http, upstream: respx.MockRouter, cls, base, path, payload
) -> None:
    upstream.get(f"{URLS[base]}{path}").respond(json=payload)
    e = await build(cls, http, URLS[base]).fetch(p.CNPJ)
    assert e.cnpj == "11.222.333/0001-81"
    assert e.razao_social == "EMPRESA EXEMPLO LTDA"
    assert e.situacao_cadastral == "ATIVA"
    assert e.data_abertura is not None
    assert e.data_abertura.isoformat() == "2010-01-05"
    assert e.capital_social == Decimal("150000")
    assert e.atividade_principal is not None
    assert e.atividade_principal.codigo == "6201501"
    assert [a.codigo for a in e.atividades_secundarias] == ["6204000"]
    assert e.socios[0].nome == "FULANA DE TAL"
    assert e.endereco.cep == "01310-100"
    assert e.telefone == "1133334444"
    assert e.fonte == cls.name


async def test_receitaws_status_error(http, upstream: respx.MockRouter) -> None:
    upstream.get(f"{URLS['receitaws']}/cnpj/{p.CNPJ}").respond(
        json={"status": "ERROR", "message": "CNPJ rejeitado pela Receita Federal"}
    )
    with pytest.raises(ProviderNotFound, match="rejeitado"):
        await build(ReceitaWs, http, URLS["receitaws"]).fetch(p.CNPJ)


async def test_5xx_retries_then_fails(http, upstream: respx.MockRouter) -> None:
    route = upstream.get(f"{URLS['viacep']}/{p.CEP}/json/").respond(503)
    with pytest.raises(ProviderUnavailable):
        await build(ViaCep, http, URLS["viacep"], retries=2).fetch(p.CEP)
    assert route.call_count == 3


async def test_5xx_followed_by_success(http, upstream: respx.MockRouter) -> None:
    upstream.get(f"{URLS['viacep']}/{p.CEP}/json/").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json=p.VIACEP)]
    )
    r = await build(ViaCep, http, URLS["viacep"]).fetch(p.CEP)
    assert r.ddd == "11"


async def test_timeout_and_4xx_errors(http, upstream: respx.MockRouter) -> None:
    upstream.get(f"{URLS['viacep']}/{p.CEP}/json/").mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(ProviderTimeout):
        await build(ViaCep, http, URLS["viacep"], retries=0).fetch(p.CEP)

    upstream.get(f"{URLS['opencep']}/{p.CEP}").respond(400)
    with pytest.raises(ProviderRejected):
        await build(OpenCep, http, URLS["opencep"]).fetch(p.CEP)

    upstream.get(f"{URLS['receitaws']}/cnpj/{p.CNPJ}").respond(429)
    with pytest.raises(QuotaExceeded):
        await build(ReceitaWs, http, URLS["receitaws"]).fetch(p.CNPJ)


async def test_circuit_opens_and_stops_calling_source(http, upstream: respx.MockRouter) -> None:
    route = upstream.get(f"{URLS['viacep']}/{p.CEP}/json/").respond(500)
    provider = build(ViaCep, http, URLS["viacep"], retries=0, threshold=2)
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await provider.fetch(p.CEP)
    assert provider.breaker.state is BreakerState.OPEN
    with pytest.raises(CircuitOpen):
        await provider.fetch(p.CEP)
    assert route.call_count == 2


async def test_404_does_not_count_as_circuit_failure(http, upstream: respx.MockRouter) -> None:
    upstream.get(f"{URLS['opencep']}/{p.CEP}").respond(404)
    provider = build(OpenCep, http, URLS["opencep"], threshold=1)
    with pytest.raises(ProviderNotFound):
        await provider.fetch(p.CEP)
    assert provider.breaker.state is BreakerState.CLOSED
