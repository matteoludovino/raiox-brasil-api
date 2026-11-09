import httpx
import respx

from tests import payloads as p
from tests.conftest import URLS, mock_cep


async def test_missing_api_key_returns_401_problem_json(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/v1/cep/{p.CEP}")
    assert r.status_code == 401
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["status"] == 401
    assert body["request_id"] == r.headers["x-request-id"]

    r = await client.get(f"/v1/cep/{p.CEP}", headers={"X-API-Key": "rx_live_falsa"})
    assert r.status_code == 401


async def test_cep_aggregated_with_merge_and_ibge(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    mock_cep(upstream)
    r = await client.get("/v1/cep/01310-100", headers=auth)
    assert r.status_code == 200, r.text
    data, meta = r.json()["data"], r.json()["meta"]

    assert data["cep"] == "01310-100"
    assert data["logradouro"] == "Avenida Paulista"
    assert data["coordenadas"] == {"latitude": -23.5629, "longitude": -46.6544}
    assert data["ddd"] == "11"
    assert data["municipio"]["regiao"] == "Sudeste"
    assert data["proveniencia"]["coordenadas"] == "brasilapi"
    assert data["proveniencia"]["codigo_ibge"] == "viacep"

    assert meta["cache"] == "MISS"
    assert {f["nome"]: f["status"] for f in meta["fontes"]} == {
        "brasilapi": "ok",
        "viacep": "ok",
        "opencep": "ok",
        "ibge": "ok",
    }
    assert all(f["latencia_ms"] is not None for f in meta["fontes"])
    assert meta["avisos"] == []
    assert r.headers["x-cache"] == "MISS"
    assert r.headers["ratelimit-remaining"] == "4"


async def test_second_call_is_hit_without_touching_sources(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    routes = mock_cep(upstream)
    await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    r = await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    assert r.headers["x-cache"] == "HIT"
    assert r.json()["meta"]["cache"] == "HIT"
    assert all(route.call_count == 1 for route in routes.values())
    assert "max-age=" in r.headers["cache-control"]


async def test_etag_304(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    mock_cep(upstream)
    first = await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    etag = first.headers["etag"]
    assert etag.startswith('W/"')
    r = await client.get(f"/v1/cep/{p.CEP}", headers={**auth, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag


async def test_invalid_cep_does_not_call_sources(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    r = await client.get("/v1/cep/123", headers=auth)
    assert r.status_code == 422
    assert r.json()["type"].endswith("entrada-invalida")
    assert upstream.calls.call_count == 0


async def test_unknown_cep_404_with_negative_cache(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    cep = "99999999"
    b = upstream.get(f"{URLS['brasilapi']}/cep/v2/{cep}").respond(404)
    v = upstream.get(f"{URLS['viacep']}/{cep}/json/").respond(json={"erro": "true"})
    o = upstream.get(f"{URLS['opencep']}/{cep}").respond(404)
    for _ in range(2):
        r = await client.get(f"/v1/cep/{cep}", headers=auth)
        assert r.status_code == 404
        assert "99999-999" in r.json()["detail"]
    assert b.call_count == v.call_count == o.call_count == 1
    assert "ratelimit-remaining" in r.headers


async def test_degraded_mode_with_one_source_down(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter
) -> None:
    mock_cep(upstream)
    upstream.get(f"{URLS['brasilapi']}/cep/v2/{p.CEP}").respond(500)
    r = await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["coordenadas"] is None  # only BrasilAPI had it
    assert body["data"]["logradouro"] == "Avenida Paulista"
    fontes = {f["nome"]: f["status"] for f in body["meta"]["fontes"]}
    assert fontes["brasilapi"] == "erro"
    assert any("brasilapi" in a for a in body["meta"]["avisos"])


async def test_all_down_503_then_stale(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter, redis
) -> None:
    # no cached copy: 503
    for base, path in [("brasilapi", "/cep/v2/{c}"), ("viacep", "/{c}/json/"), ("opencep", "/{c}")]:
        upstream.get(URLS[base] + path.format(c="22041001")).respond(503)
    r = await client.get("/v1/cep/22041001", headers=auth)
    assert r.status_code == 503
    assert r.json()["type"].endswith("fontes-indisponiveis")

    # with an expired copy: STALE
    mock_cep(upstream)
    await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    await _expire(redis, f"raiox:v1:cep:{p.CEP}")
    for base, path in [("brasilapi", "/cep/v2/{c}"), ("viacep", "/{c}/json/"), ("opencep", "/{c}")]:
        upstream.get(URLS[base] + path.format(c=p.CEP)).respond(503)
    r = await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    assert r.status_code == 200
    assert r.headers["x-cache"] == "STALE"
    assert r.headers["cache-control"] == "private, max-age=0"
    assert "stale-if-error" in r.json()["meta"]["avisos"][0]


async def test_ibge_down_falls_back_to_snapshot_with_full_ttl(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter, redis, settings
) -> None:
    mock_cep(upstream)
    upstream.get(f"{URLS['ibge']}/localidades/municipios/{p.IBGE_SP}").respond(500)
    r = await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["municipio"]["mesorregiao"] == "Metropolitana de São Paulo"
    sources = {f["nome"]: f["status"] for f in body["meta"]["fontes"]}
    assert sources["ibge"] == "erro"
    assert sources["ibge:snapshot"] == "ok"
    assert any("snapshot" in w for w in body["meta"]["avisos"])
    # Data is complete (snapshot covered it): cached with the full CEP TTL, not the short one.
    assert await _freshness(redis, f"raiox:v1:cep:{p.CEP}") > settings.ttl_degraded_s


async def test_ibge_down_and_code_missing_from_snapshot_is_degraded(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter, redis, settings
) -> None:
    cep, code = "01001000", "9999999"
    upstream.get(f"{URLS['brasilapi']}/cep/v2/{cep}").respond(json=p.BRASILAPI_CEP)
    upstream.get(f"{URLS['viacep']}/{cep}/json/").respond(json={**p.VIACEP, "ibge": code})
    upstream.get(f"{URLS['opencep']}/{cep}").respond(json={**p.OPENCEP, "ibge": code})
    upstream.get(f"{URLS['ibge']}/localidades/municipios/{code}").respond(503)

    r = await client.get(f"/v1/cep/{cep}", headers=auth)
    assert r.status_code == 200
    assert r.json()["data"]["municipio"] is None
    assert any("IBGE" in w for w in r.json()["meta"]["avisos"])
    assert await _freshness(redis, f"raiox:v1:cep:{cep}") <= settings.ttl_degraded_s


async def test_slow_ibge_is_cut_by_enrichment_deadline(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter, settings
) -> None:
    import asyncio
    import time

    mock_cep(upstream)

    async def slow_ibge(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=p.IBGE_MUNICIPIO)

    upstream.get(f"{URLS['ibge']}/localidades/municipios/{p.IBGE_SP}").mock(side_effect=slow_ibge)
    settings.enrichment_deadline_s = 0.3

    start = time.perf_counter()
    r = await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    assert time.perf_counter() - start < 1.5  # did not wait for IBGE
    assert r.status_code == 200
    assert r.json()["data"]["municipio"]["regiao"] == "Sudeste"  # from the snapshot
    ibge = next(f for f in r.json()["meta"]["fontes"] if f["nome"] == "ibge")
    assert ibge["detalhe"] == "deadline"


async def test_partial_result_is_cached_with_short_ttl(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter, redis, settings
) -> None:
    mock_cep(upstream)
    upstream.get(f"{URLS['brasilapi']}/cep/v2/{p.CEP}").respond(500)
    assert (await client.get(f"/v1/cep/{p.CEP}", headers=auth)).status_code == 200
    assert await _freshness(redis, f"raiox:v1:cep:{p.CEP}") <= settings.ttl_degraded_s


async def test_healthy_result_is_cached_with_full_ttl(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter, redis, settings
) -> None:
    mock_cep(upstream)
    assert (await client.get(f"/v1/cep/{p.CEP}", headers=auth)).status_code == 200
    assert await _freshness(redis, f"raiox:v1:cep:{p.CEP}") > settings.ttl_cep_s - 60


async def _freshness(redis, key: str) -> float:
    import json
    import time

    return float(json.loads(await redis.get(key))["fresh_until"]) - time.time()


async def _expire(redis, key: str) -> None:
    import json

    entry = json.loads(await redis.get(key))
    entry["fresh_until"] = 0
    await redis.set(key, json.dumps(entry))


async def test_slow_source_cut_by_deadline(
    client: httpx.AsyncClient, auth: dict[str, str], upstream: respx.MockRouter, settings
) -> None:
    import asyncio
    import time

    mock_cep(upstream)

    async def slow_source(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=p.OPENCEP)

    upstream.get(f"{URLS['opencep']}/{p.CEP}").mock(side_effect=slow_source)
    settings.aggregate_deadline_s = 0.2

    start = time.perf_counter()
    r = await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    assert time.perf_counter() - start < 1.5  # did not wait for the slow source
    assert r.status_code == 200
    fontes = {f["nome"]: f for f in r.json()["meta"]["fontes"]}
    assert fontes["opencep"]["status"] == "timeout"
    assert fontes["opencep"]["detalhe"] == "deadline da agregação"
    assert r.json()["data"]["codigo_ibge"] == p.IBGE_SP  # came from ViaCEP
