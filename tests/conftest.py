from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI

from raiox.core.config import Settings
from raiox.main import create_app
from tests import payloads as p

ADMIN_TOKEN = "test-admin-token"

URLS = {
    "brasilapi": "https://brasilapi.test/api",
    "viacep": "https://viacep.test/ws",
    "opencep": "https://opencep.test/v1",
    "ibge": "https://ibge.test/api/v1",
    "cnpjws": "https://cnpjws.test",
    "receitaws": "https://receitaws.test/v1",
}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        log_json=False,
        log_level="WARNING",
        admin_token=ADMIN_TOKEN,  # type: ignore[arg-type]
        brasilapi_url=URLS["brasilapi"],
        viacep_url=URLS["viacep"],
        opencep_url=URLS["opencep"],
        ibge_url=URLS["ibge"],
        cnpjws_url=URLS["cnpjws"],
        receitaws_url=URLS["receitaws"],
        upstream_timeout_s=1,
        upstream_retries=1,
        retry_base_delay_s=0,
        retry_max_delay_s=0,
        aggregate_deadline_s=1,
        rate_limit_free=5,
        rate_limit_pro=100,
        cache_lock_wait_s=1,
    )


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


@pytest.fixture
def upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def app(
    settings: Settings, redis: FakeAsyncRedis, upstream: respx.MockRouter
) -> AsyncIterator[FastAPI]:
    async with httpx.AsyncClient() as http:
        application = create_app(settings, redis=redis, http_client=http)
        async with LifespanManager(application):
            yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def create_key(client: httpx.AsyncClient, plan: str = "free") -> str:
    r = await client.post(
        "/admin/api-keys",
        json={"name": f"teste-{plan}", "plan": plan},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["key"])


@pytest.fixture
async def auth(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-API-Key": await create_key(client)}


@pytest.fixture
async def auth_pro(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-API-Key": await create_key(client, "pro")}


def mock_cep(router: respx.MockRouter, cep: str = p.CEP) -> dict[str, respx.Route]:
    """Registers successful responses for every CEP source plus IBGE."""
    return {
        "brasilapi": router.get(f"{URLS['brasilapi']}/cep/v2/{cep}").respond(json=p.BRASILAPI_CEP),
        "viacep": router.get(f"{URLS['viacep']}/{cep}/json/").respond(json=p.VIACEP),
        "opencep": router.get(f"{URLS['opencep']}/{cep}").respond(json=p.OPENCEP),
        "ibge": router.get(f"{URLS['ibge']}/localidades/municipios/{p.IBGE_SP}").respond(
            json=p.IBGE_MUNICIPIO
        ),
    }


def mock_cnpj(router: respx.MockRouter, cnpj: str = p.CNPJ) -> dict[str, respx.Route]:
    return {
        "brasilapi": router.get(f"{URLS['brasilapi']}/cnpj/v1/{cnpj}").respond(
            json=p.BRASILAPI_CNPJ
        ),
        "cnpjws": router.get(f"{URLS['cnpjws']}/cnpj/{cnpj}").respond(json=p.CNPJWS),
        "receitaws": router.get(f"{URLS['receitaws']}/cnpj/{cnpj}").respond(json=p.RECEITAWS),
    }
