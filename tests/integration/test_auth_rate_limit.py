import httpx
import respx
from pydantic import SecretStr

from tests import payloads as p
from tests.conftest import ADMIN_TOKEN, mock_cep

ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


async def test_rate_limit_per_plan(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    auth_pro: dict[str, str],
    upstream: respx.MockRouter,
) -> None:
    mock_cep(upstream)
    statuses = [(await client.get(f"/v1/cep/{p.CEP}", headers=auth)).status_code for _ in range(6)]
    assert statuses == [200] * 5 + [429]

    r = await client.get(f"/v1/cep/{p.CEP}", headers=auth)
    assert r.status_code == 429
    assert r.headers["content-type"] == "application/problem+json"
    assert int(r.headers["retry-after"]) > 0
    assert r.headers["ratelimit-remaining"] == "0"
    assert "free" in r.json()["detail"]

    # the pro key has its own, higher limit
    r = await client.get(f"/v1/cep/{p.CEP}", headers=auth_pro)
    assert r.status_code == 200
    assert r.headers["ratelimit-limit"] == "100"


async def test_admin_creates_lists_and_revokes(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    mock_cep(upstream)
    r = await client.post("/admin/api-keys", json={"name": "acme", "plan": "pro"}, headers=ADMIN)
    assert r.status_code == 201
    created = r.json()
    assert created["key"].startswith("rx_live_")
    assert created["key"].startswith(created["prefix"])

    listed = (await client.get("/admin/api-keys", headers=ADMIN)).json()
    assert [k["id"] for k in listed] == [created["id"]]
    assert "key" not in listed[0]

    headers = {"X-API-Key": created["key"]}
    assert (await client.get(f"/v1/cep/{p.CEP}", headers=headers)).status_code == 200

    assert (
        await client.delete(f"/admin/api-keys/{created['id']}", headers=ADMIN)
    ).status_code == 204
    assert (
        await client.delete(f"/admin/api-keys/{created['id']}", headers=ADMIN)
    ).status_code == 404
    assert (await client.get(f"/v1/cep/{p.CEP}", headers=headers)).status_code == 401


async def test_key_stored_only_as_hash(client: httpx.AsyncClient, redis) -> None:
    r = await client.post("/admin/api-keys", json={"name": "acme"}, headers=ADMIN)
    raw = r.json()["key"]
    dump = " ".join([k async for k in redis.scan_iter("*")])
    for k in await redis.keys("*apikey:*"):
        if await redis.type(k) == "hash":
            dump += " ".join((await redis.hgetall(k)).values())
    assert raw not in dump


async def test_admin_requires_token(client: httpx.AsyncClient) -> None:
    assert (await client.post("/admin/api-keys", json={"name": "x1"})).status_code == 401
    r = await client.post(
        "/admin/api-keys", json={"name": "x1"}, headers={"Authorization": "Bearer errado"}
    )
    assert r.status_code == 401
    r = await client.post("/admin/api-keys", json={"name": "x", "plan": "gold"}, headers=ADMIN)
    assert r.status_code == 422
    assert r.json()["errors"]


async def test_demo_key_is_seeded_at_startup_with_its_own_limit(
    settings, redis, upstream: respx.MockRouter
) -> None:
    from asgi_lifespan import LifespanManager

    from raiox.main import create_app

    demo_key = "rx_live_demo_aaaaaaaaaaaaaaaaaaaa"
    demo_settings = settings.model_copy(
        update={"demo_api_key": SecretStr(demo_key), "rate_limit_demo": 2}
    )
    mock_cep(upstream)
    async with httpx.AsyncClient() as http:
        app = create_app(demo_settings, redis=redis, http_client=http)
        async with (
            LifespanManager(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            headers = {"X-API-Key": demo_key}
            statuses = [
                (await client.get(f"/v1/cep/{p.CEP}", headers=headers)).status_code
                for _ in range(3)
            ]
            assert statuses == [200, 200, 429]

            r = await client.get(f"/v1/cep/{p.CEP}", headers=headers)
            assert r.headers["ratelimit-limit"] == "2"
            assert "demo" in r.json()["detail"]

            keys = (await client.get("/admin/api-keys", headers=ADMIN)).json()
            assert [(k["id"], k["plan"]) for k in keys] == [("k_demo", "demo")]

            # Redis reset after startup: the demo key self-heals on the next request.
            await redis.flushall()
            r = await client.get(f"/v1/cep/{p.CEP}", headers=headers)
            assert r.status_code == 200
            # ...but a random key is still rejected.
            r = await client.get(f"/v1/cep/{p.CEP}", headers={"X-API-Key": demo_key + "x"})
            assert r.status_code == 401
