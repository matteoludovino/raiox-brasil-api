"""API keys: generation, hashed storage and verification.

* The plaintext key is shown exactly once, at creation; Redis only stores its SHA-256.
* SHA-256 (not bcrypt/argon2) because the key carries 256 bits of random entropy: there is
  nothing to brute-force with a dictionary, and lookup is O(1) by the digest itself. Slow
  hashes are for passwords, which are low-entropy. See ADR 0005.
* A readable prefix (``rx_live_``) makes leaked keys easy to detect (secret scanning).

Redis layout (no TTL, so the volatile-lru policy never evicts it):
    {prefix}:apikey:{sha256}  -> hash {id, name, plan, prefix, created_at}
    {prefix}:apikey_id:{id}   -> sha256
    {prefix}:apikeys          -> set of ids
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from raiox.core.config import Plan

KEY_PREFIX = "rx_live_"


class ApiKeyInfo(BaseModel):
    id: str = Field(examples=["k_3f9a1c2b"])
    name: str = Field(examples=["acme-client"])
    plan: Plan
    prefix: str = Field(
        description="Start of the key, for identification", examples=["rx_live_Ab3dE"]
    )
    created_at: datetime


class ApiKeyCreated(ApiKeyInfo):
    key: str = Field(description="Shown only once. Store it somewhere safe.")


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKeyStore:
    def __init__(self, redis: Redis, *, prefix: str) -> None:
        # decode_responses=True: the client returns str; Any avoids a bytes/str false positive.
        self._r: Any = redis
        self._p = prefix

    async def create(self, name: str, plan: Plan) -> ApiKeyCreated:
        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        info = ApiKeyCreated(
            id=f"k_{secrets.token_hex(4)}",
            name=name,
            plan=plan,
            prefix=raw[: len(KEY_PREFIX) + 5],
            created_at=datetime.now(UTC),
            key=raw,
        )
        digest = hash_key(raw)
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(
                f"{self._p}:apikey:{digest}",
                mapping={
                    k: str(v) for k, v in info.model_dump(mode="json", exclude={"key"}).items()
                },
            )
            pipe.set(f"{self._p}:apikey_id:{info.id}", digest)
            pipe.sadd(f"{self._p}:apikeys", info.id)
            await pipe.execute()
        return info

    async def verify(self, raw: str) -> ApiKeyInfo | None:
        if not raw.startswith(KEY_PREFIX):
            return None
        data = await self._r.hgetall(f"{self._p}:apikey:{hash_key(raw)}")
        return ApiKeyInfo.model_validate(data) if data else None

    async def list(self) -> list[ApiKeyInfo]:
        ids = sorted(await self._r.smembers(f"{self._p}:apikeys"))
        result: list[ApiKeyInfo] = []
        for key_id in ids:
            digest = await self._r.get(f"{self._p}:apikey_id:{key_id}")
            if digest and (data := await self._r.hgetall(f"{self._p}:apikey:{digest}")):
                result.append(ApiKeyInfo.model_validate(data))
        return result

    async def revoke(self, key_id: str) -> bool:
        digest = await self._r.get(f"{self._p}:apikey_id:{key_id}")
        if not digest:
            return False
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.delete(f"{self._p}:apikey:{digest}", f"{self._p}:apikey_id:{key_id}")
            pipe.srem(f"{self._p}:apikeys", key_id)
            await pipe.execute()
        return True
