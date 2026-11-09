import httpx
import pytest

from raiox.core.config import Settings


async def test_health_ready_metrics(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}

    ready = (await client.get("/ready")).json()
    assert ready["redis"] is True
    assert set(ready["circuitos"].values()) == {"closed"}

    await client.get("/health")
    metrics = (await client.get("/metrics")).text
    assert "raiox_http_requests_total" in metrics
    assert 'route="/health"' in metrics


async def test_request_id_propagated(client: httpx.AsyncClient) -> None:
    r = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    r = await client.get("/health", headers={"X-Request-ID": "<script>"})
    assert r.headers["x-request-id"] != "<script>"


async def test_unknown_route_returns_problem_json(client: httpx.AsyncClient) -> None:
    r = await client.get("/nao-existe")
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"


async def test_openapi_documents_security_and_errors(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert "APIKeyHeader" in spec["components"]["securitySchemes"]
    cep = spec["paths"]["/v1/cep/{cep}"]["get"]
    assert "/v1/empresas/{cnpj}" in spec["paths"]
    assert {"200", "401", "404", "422", "429", "503"} <= set(cep["responses"])


def test_weak_admin_token_rejected_in_production() -> None:
    with pytest.raises(ValueError, match="32"):
        Settings(_env_file=None, env="prod")  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", ["demo-key-without-prefix-123456", "rx_live_short"])
def test_invalid_demo_key_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="RAIOX_DEMO_API_KEY"):
        Settings(_env_file=None, demo_api_key=bad)  # type: ignore[call-arg, arg-type]
