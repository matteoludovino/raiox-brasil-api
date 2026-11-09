"""CNPJ aggregation: fallback chain plus address enrichment.

Why fallback instead of fan-out? CNPJ.ws and ReceitaWS allow ~3 req/min. Calling every source
on every lookup would burn the quota for nothing, since the registry data is the same (it all
comes from the Federal Revenue). The next source is only called if the previous one fails.
See ADR 0001.

Warnings and error details are returned to API clients, so they stay in Portuguese.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Protocol

from raiox.core.config import Settings
from raiox.core.errors import AppError, NotFoundError, UpstreamUnavailableError
from raiox.domain.models import Address, Aggregate, Company, SourceInfo, SourceStatus
from raiox.domain.validators import format_cnpj, is_alphanumeric_cnpj, normalize_cep
from raiox.infra.cache import Cache, CachedValue
from raiox.services.cep_service import CepService
from raiox.services.sources import ms_since, observe


class CnpjProvider(Protocol):
    name: str

    async def fetch(self, cnpj: str) -> Company: ...


class CompanyService:
    def __init__(
        self,
        chain: Sequence[CnpjProvider],
        cep_service: CepService,
        cache: Cache,
        settings: Settings,
    ) -> None:
        self._chain = list(chain)
        self._cep = cep_service
        self._cache = cache
        self._s = settings

    async def lookup(self, cnpj: str) -> CachedValue[Aggregate[Company]]:
        return await self._cache.get_or_load(
            f"cnpj:{cnpj}",
            Aggregate[Company],
            lambda: self._load(cnpj),
            ttl_s=self._s.ttl_cnpj_s,
            negative_ttl_s=self._s.ttl_negative_s,
            stale_ttl_s=self._s.ttl_stale_s,
        )

    async def _load(self, cnpj: str) -> Aggregate[Company]:
        sources: list[SourceInfo] = []
        warnings: list[str] = []
        company: Company | None = None

        for provider in self._chain:
            if company is not None:
                sources.append(
                    SourceInfo(
                        nome=provider.name,
                        status=SourceStatus.SKIPPED,
                        detalhe="não necessária: fonte anterior respondeu",
                    )
                )
                continue
            # A 404 from one source does not end the chain: databases lag differently.
            company, info = await observe(provider.name, provider.fetch(cnpj))
            sources.append(info)

        if company is None:
            if any(s.status is SourceStatus.NOT_FOUND for s in sources):
                raise NotFoundError(f"CNPJ {format_cnpj(cnpj)} não encontrado")
            if is_alphanumeric_cnpj(cnpj) and any(
                s.status is SourceStatus.REJECTED for s in sources
            ):
                raise UpstreamUnavailableError(
                    "As fontes ainda não aceitam CNPJ alfanumérico para este número."
                )
            raise UpstreamUnavailableError("Nenhuma fonte de CNPJ disponível no momento")

        if company.endereco.cep:
            company.localizacao, cep_info = await self._enrich(company.endereco.cep, warnings)
            sources.append(cep_info)

        return Aggregate[Company](data=company, fontes=sources, avisos=warnings)

    async def _enrich(self, cep: str, warnings: list[str]) -> tuple[Address | None, SourceInfo]:
        """Reuses the CEP aggregator (and its cache). A failure here degrades, never breaks."""
        start = time.perf_counter()
        try:
            agg = await self._cep.lookup(normalize_cep(cep))
        except AppError as exc:
            warnings.append(f"Endereço não enriquecido: {exc.detail or exc.title}.")
            return None, SourceInfo(
                nome="raiox:cep",
                status=SourceStatus.ERROR,
                latencia_ms=ms_since(start),
                detalhe=exc.detail,
            )
        warnings.extend(f"[cep] {w}" for w in agg.value.avisos)
        return agg.value.data, SourceInfo(
            nome="raiox:cep",
            status=SourceStatus.OK,
            latencia_ms=ms_since(start),
            detalhe=f"cache {agg.status}",
        )
