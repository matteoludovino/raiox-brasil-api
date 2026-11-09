import httpx
import respx

from tests import payloads as p
from tests.conftest import URLS, mock_cep, mock_cnpj


async def test_first_source_answers_and_others_are_skipped(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    routes = mock_cnpj(upstream)
    mock_cep(upstream)
    r = await client.get("/v1/empresas/11.222.333/0001-81", headers=auth)
    assert r.status_code == 200, r.text
    data, meta = r.json()["data"], r.json()["meta"]

    assert data["razao_social"] == "EMPRESA EXEMPLO LTDA"
    assert data["fonte"] == "brasilapi"
    assert data["capital_social"] == "150000"
    assert data["localizacao"]["municipio"]["codigo_ibge"] == p.IBGE_SP
    assert data["localizacao"]["coordenadas"] is not None

    fontes = {f["nome"]: f["status"] for f in meta["fontes"]}
    assert fontes == {
        "brasilapi": "ok",
        "cnpjws": "ignorada",
        "receitaws": "ignorada",
        "raiox:cep": "ok",
    }
    assert routes["cnpjws"].call_count == 0
    assert routes["receitaws"].call_count == 0


async def test_fallback_when_primary_fails(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    mock_cnpj(upstream)
    mock_cep(upstream)
    upstream.get(f"{URLS['brasilapi']}/cnpj/v1/{p.CNPJ}").respond(502)
    r = await client.get(f"/v1/empresas/{p.CNPJ}", headers=auth)
    assert r.status_code == 200
    assert r.json()["data"]["fonte"] == "cnpjws"
    fontes = {f["nome"]: f["status"] for f in r.json()["meta"]["fontes"]}
    assert fontes["brasilapi"] == "erro"
    assert fontes["receitaws"] == "ignorada"


async def test_outbound_quota_protects_limited_source(
    client: httpx.AsyncClient, auth_pro: dict[str, str], upstream: respx.MockRouter, settings
) -> None:
    """With BrasilAPI down, CNPJ.ws is only called up to its quota (3/min); then ReceitaWS."""
    mock_cep(upstream)
    upstream.get(url__regex=rf"{URLS['brasilapi']}/cnpj/v1/.*").respond(503)
    cnpjws = upstream.get(url__regex=rf"{URLS['cnpjws']}/cnpj/.*").respond(json=p.CNPJWS)
    receitaws = upstream.get(url__regex=rf"{URLS['receitaws']}/cnpj/.*").respond(json=p.RECEITAWS)

    cnpjs = ["11222333000181", "00000000000191", "11444777000161", "60701190000104"]
    sources_used = []
    for cnpj in cnpjs:
        r = await client.get(f"/v1/empresas/{cnpj}", headers=auth_pro)
        assert r.status_code == 200, r.text
        sources_used.append(r.json()["data"]["fonte"])

    assert cnpjws.call_count == settings.outbound_cnpjws_per_min
    assert sources_used[-1] == "receitaws"
    assert receitaws.call_count == 1


async def test_unknown_cnpj(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    upstream.get(f"{URLS['brasilapi']}/cnpj/v1/{p.CNPJ}").respond(404)
    upstream.get(f"{URLS['cnpjws']}/cnpj/{p.CNPJ}").respond(404)
    upstream.get(f"{URLS['receitaws']}/cnpj/{p.CNPJ}").respond(
        json={"status": "ERROR", "message": "CNPJ inválido"}
    )
    r = await client.get(f"/v1/empresas/{p.CNPJ}", headers=auth)
    assert r.status_code == 404


async def test_alphanumeric_cnpj_validated_locally_and_rejected_by_sources(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    alnum = "12ABC34501DE35"
    upstream.get(f"{URLS['brasilapi']}/cnpj/v1/{alnum}").respond(400)
    upstream.get(f"{URLS['cnpjws']}/cnpj/{alnum}").respond(400)
    upstream.get(f"{URLS['receitaws']}/cnpj/{alnum}").respond(400)
    r = await client.get("/v1/empresas/12.abc.345/01de-35", headers=auth)
    assert r.status_code == 503
    assert "alfanumérico" in r.json()["detail"]

    r = await client.get("/v1/empresas/12.ABC.345/01DE-36", headers=auth)
    assert r.status_code == 422


async def test_enrichment_failure_degrades(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    mock_cnpj(upstream)
    for base, path in [("brasilapi", "/cep/v2/{c}"), ("viacep", "/{c}/json/"), ("opencep", "/{c}")]:
        upstream.get(URLS[base] + path.format(c=p.CEP)).respond(500)
    r = await client.get(f"/v1/empresas/{p.CNPJ}", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["localizacao"] is None
    assert body["data"]["endereco"]["cep"] == "01310-100"
    assert any("não enriquecido" in a for a in body["meta"]["avisos"])
