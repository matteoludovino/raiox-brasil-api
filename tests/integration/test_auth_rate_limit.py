import httpx
import respx

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
