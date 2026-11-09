"""CEP aggregation: parallel fan-out, field-by-field merge and IBGE enrichment.

Why fan-out instead of fallback? CEP sources are free, have no tight quota and are
**complementary** (BrasilAPI has coordinates; ViaCEP has IBGE code and area code). Calling them
all in parallel costs the latency of the slowest one (capped by a deadline) and yields the most
complete record. See ADR 0001.

Warnings and error details are returned to API clients, so they stay in Portuguese.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Protocol

from raiox.core.config import Settings
from raiox.core.errors import NotFoundError, UpstreamUnavailableError
from raiox.domain.merge import merge_cep
from raiox.domain.models import (
    Address,
    AddressPartial,
    Aggregate,
    Municipality,
    SourceInfo,
    SourceStatus,
)
from raiox.domain.validators import format_cep
from raiox.infra.cache import Cache, CachedValue
from raiox.providers.cep import IbgeMunicipalities
from raiox.services.sources import ms_since, observe


class CepProvider(Protocol):
    name: str

    async def fetch(self, cep: str) -> AddressPartial: ...


class CepService:
    def __init__(
        self,
        providers: Sequence[CepProvider],
        ibge: IbgeMunicipalities,
        cache: Cache,
        settings: Settings,
    ) -> None:
        self._providers = list(providers)  # order = merge precedence
        self._ibge = ibge
        self._cache = cache
        self._s = settings

    async def lookup(self, cep: str) -> CachedValue[Aggregate[Address]]:
        return await self._cache.get_or_load(
            f"cep:{cep}",
            Aggregate[Address],
            lambda: self._load(cep),
            ttl_s=self._s.ttl_cep_s,
            negative_ttl_s=self._s.ttl_negative_s,
            stale_ttl_s=self._s.ttl_stale_s,
        )

    async def _load(self, cep: str) -> Aggregate[Address]:
        parts, sources = await self._fan_out(cep)

        if not parts:
            if any(s.status is SourceStatus.NOT_FOUND for s in sources):
                raise NotFoundError(f"CEP {format_cep(cep)} não encontrado")
            raise UpstreamUnavailableError("Nenhuma fonte de CEP disponível no momento")

        fields, provenance, warnings = merge_cep(parts)
        failed = [s.nome for s in sources if s.status is not SourceStatus.OK]
        if failed:
            warnings.append(f"Resposta parcial: fontes sem sucesso: {', '.join(failed)}.")

        municipality: Municipality | None = None
        if code := fields.get("codigo_ibge"):
            municipality, ibge_info = await self._municipality(code)
            sources.append(ibge_info)
            if municipality is None:
                warnings.append("Dados do município (IBGE) indisponíveis no momento.")
        else:
            warnings.append("Código IBGE não informado pelas fontes; município não enriquecido.")

        address = Address(
            cep=format_cep(cep), municipio=municipality, proveniencia=provenance, **fields
        )
        return Aggregate[Address](data=address, fontes=sources, avisos=warnings)

    async def _fan_out(self, cep: str) -> tuple[list[tuple[str, AddressPartial]], list[SourceInfo]]:
        tasks = {
            p.name: asyncio.create_task(observe(p.name, p.fetch(cep))) for p in self._providers
        }
        _, pending = await asyncio.wait(tasks.values(), timeout=self._s.aggregate_deadline_s)
        for task in pending:
            task.cancel()

        parts: list[tuple[str, AddressPartial]] = []
        sources: list[SourceInfo] = []
        for name, task in tasks.items():  # iterate in precedence order
            if task in pending:
                sources.append(
                    SourceInfo(
                        nome=name, status=SourceStatus.TIMEOUT, detalhe="deadline da agregação"
                    )
                )
                continue
            result, info = task.result()
            sources.append(info)
            if result is not None:
                parts.append((name, result))
        return parts, sources

    async def _municipality(self, code: str) -> tuple[Municipality | None, SourceInfo]:
        """Municipalities almost never change: own 30-day cache, shared across CEPs."""

        async def load() -> Municipality:
            result, info = await observe(self._ibge.name, self._ibge.get_municipality(code))
            if result is None:
                if info.status is SourceStatus.NOT_FOUND:
                    raise NotFoundError(info.detalhe)
                raise UpstreamUnavailableError(info.detalhe)
            return result

        start = time.perf_counter()
        try:
            cached = await self._cache.get_or_load(
                f"ibge:municipio:{code}",
                Municipality,
                load,
                ttl_s=self._s.ttl_ibge_s,
                negative_ttl_s=self._s.ttl_negative_s,
                stale_ttl_s=self._s.ttl_stale_s,
            )
        except (NotFoundError, UpstreamUnavailableError) as exc:
            status = (
                SourceStatus.NOT_FOUND if isinstance(exc, NotFoundError) else SourceStatus.ERROR
            )
            return None, SourceInfo(
                nome="ibge", status=status, latencia_ms=ms_since(start), detalhe=exc.detail
            )
        return cached.value, SourceInfo(
            nome="ibge",
            status=SourceStatus.OK,
            latencia_ms=ms_since(start),
            detalhe=f"cache {cached.status}",
        )
