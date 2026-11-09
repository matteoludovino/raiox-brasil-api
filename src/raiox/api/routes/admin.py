"""API key management (protected by the admin token)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from pydantic import BaseModel, Field

from raiox.api.deps import ContainerDep, require_admin
from raiox.core.config import Plan
from raiox.core.errors import ERROR_RESPONSES, NotFoundError
from raiox.security.api_keys import ApiKeyCreated, ApiKeyInfo

router = APIRouter(prefix="/admin/api-keys", tags=["Admin"], dependencies=[Depends(require_admin)])


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, examples=["acme-client"])
    plan: Plan = "free"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiKeyCreated,
    summary="Create an API key (plaintext shown only once)",
    responses={401: ERROR_RESPONSES[401]},
)
async def create_key(body: ApiKeyCreate, c: ContainerDep) -> ApiKeyCreated:
    return await c.keys.create(body.name, body.plan)


@router.get("", response_model=list[ApiKeyInfo], summary="List API keys (without the secret)")
async def list_keys(c: ContainerDep) -> list[ApiKeyInfo]:
    return await c.keys.list()


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
    responses={404: ERROR_RESPONSES[404]},
)
async def revoke_key(
    c: ContainerDep, key_id: Annotated[str, Path(examples=["k_3f9a1c2b"])]
) -> Response:
    if not await c.keys.revoke(key_id):
        raise NotFoundError(f"API key {key_id} não encontrada")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
