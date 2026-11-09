from fakeredis import FakeAsyncRedis

from raiox.security.api_keys import ApiKeyStore

DEMO = "rx_live_demo_aaaaaaaaaaaaaaaaaaaa"
ROTATED = "rx_live_demo_bbbbbbbbbbbbbbbbbbbb"


async def test_ensure_is_idempotent(redis: FakeAsyncRedis) -> None:
    store = ApiKeyStore(redis, prefix="t")
    first = await store.ensure(DEMO, key_id="k_demo", name="public-demo", plan="demo")
    second = await store.ensure(DEMO, key_id="k_demo", name="public-demo", plan="demo")

    assert first.created_at == second.created_at  # not re-created on every startup
    assert [k.id for k in await store.list()] == ["k_demo"]
    verified = await store.verify(DEMO)
    assert verified is not None
    assert verified.plan == "demo"


async def test_ensure_revokes_previous_key_on_rotation(redis: FakeAsyncRedis) -> None:
    store = ApiKeyStore(redis, prefix="t")
    await store.ensure(DEMO, key_id="k_demo", name="public-demo", plan="demo")
    await store.ensure(ROTATED, key_id="k_demo", name="public-demo", plan="demo")

    assert await store.verify(DEMO) is None
    assert await store.verify(ROTATED) is not None
    assert [k.id for k in await store.list()] == ["k_demo"]


async def test_ensure_does_not_touch_other_keys(redis: FakeAsyncRedis) -> None:
    store = ApiKeyStore(redis, prefix="t")
    regular = await store.create("acme", "pro")
    await store.ensure(DEMO, key_id="k_demo", name="public-demo", plan="demo")
    assert await store.verify(regular.key) is not None
