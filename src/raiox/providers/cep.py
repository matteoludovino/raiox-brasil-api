"""CEP adapters: BrasilAPI v2, ViaCEP and OpenCEP, plus IBGE (municipalities)."""

from __future__ import annotations

from typing import Any

from raiox.domain.models import AddressPartial, Municipality
from raiox.providers.base import BaseProvider, ProviderBadResponse, ProviderNotFound
from raiox.providers.parsing import coordinates, digits, text


class BrasilApiCep(BaseProvider):
    """Strength: geographic coordinates."""

    name = "brasilapi"

    async def fetch(self, cep: str) -> AddressPartial:
        d = await self._get_json(f"/cep/v2/{cep}")
        if not isinstance(d, dict):
            raise ProviderBadResponse("brasilapi: formato inesperado")
        coords = ((d.get("location") or {}).get("coordinates")) or {}
        return AddressPartial(
            logradouro=text(d.get("street")),
            bairro=text(d.get("neighborhood")),
            cidade=text(d.get("city")),
            uf=text(d.get("state")),
            coordenadas=coordinates(coords.get("latitude"), coords.get("longitude")),
        )


class ViaCep(BaseProvider):
    """Strength: IBGE code, area code (DDD) and address complement."""

    name = "viacep"

    async def fetch(self, cep: str) -> AddressPartial:
        d = await self._get_json(f"/{cep}/json/")
        if not isinstance(d, dict):
            raise ProviderBadResponse("viacep: formato inesperado")
        if d.get("erro"):  # ViaCEP answers 200 {"erro": "true"} for a non-existent CEP
            raise ProviderNotFound("viacep: não encontrado")
        return _viacep_style_partial(d)


class OpenCep(BaseProvider):
    name = "opencep"

    async def fetch(self, cep: str) -> AddressPartial:
        d = await self._get_json(f"/{cep}")
        if not isinstance(d, dict) or d.get("error"):
            raise ProviderNotFound("opencep: não encontrado")
        return _viacep_style_partial(d)


def _viacep_style_partial(d: dict[str, Any]) -> AddressPartial:
    return AddressPartial(
        logradouro=text(d.get("logradouro")),
        complemento=text(d.get("complemento")),
        bairro=text(d.get("bairro")),
        cidade=text(d.get("localidade")),
        uf=text(d.get("uf")),
        codigo_ibge=digits(d.get("ibge")),
        ddd=text(d.get("ddd")),
    )


class IbgeMunicipalities(BaseProvider):
    name = "ibge"

    async def get_municipality(self, code: str) -> Municipality:
        d = await self._get_json(f"/localidades/municipios/{code}")
        if not d or not isinstance(d, dict):  # IBGE answers 200 [] for an unknown code
            raise ProviderNotFound("ibge: município não encontrado")

        micro = d.get("microrregiao") or {}
        meso = micro.get("mesorregiao") or {}
        immediate = d.get("regiao-imediata") or {}
        intermediate = immediate.get("regiao-intermediaria") or {}
        # Some newer municipalities have no microregion: fall back to the immediate region.
        uf = meso.get("UF") or intermediate.get("UF") or {}
        return Municipality(
            codigo_ibge=str(d.get("id", code)),
            nome=str(d.get("nome", "")),
            uf=text(uf.get("sigla")),
            microrregiao=text(micro.get("nome")),
            mesorregiao=text(meso.get("nome")),
            regiao_imediata=text(immediate.get("nome")),
            regiao_intermediaria=text(intermediate.get("nome")),
            regiao=text((uf.get("regiao") or {}).get("nome")),
        )
