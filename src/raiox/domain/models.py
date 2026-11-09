"""Domain models (the public API contract) and partial models produced by each source.

Class names and docs are in English; **field names and enum values are in Portuguese**
because they are the JSON contract consumed by Brazilian clients.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


# --- Provenance metadata -------------------------------------------------------
class SourceStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "nao_encontrado"
    ERROR = "erro"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuito_aberto"
    QUOTA_EXCEEDED = "cota_esgotada"
    REJECTED = "rejeitado"
    SKIPPED = "ignorada"


# Failures that may go away on their own (as opposed to "not found" or "rejected").
TRANSIENT_FAILURES = frozenset(
    {
        SourceStatus.ERROR,
        SourceStatus.TIMEOUT,
        SourceStatus.CIRCUIT_OPEN,
        SourceStatus.QUOTA_EXCEEDED,
    }
)


class CacheStatus(StrEnum):
    HIT = "HIT"
    MISS = "MISS"
    STALE = "STALE"


class SourceInfo(BaseModel):
    nome: str = Field(description="Source name", examples=["viacep"])
    status: SourceStatus
    latencia_ms: float | None = Field(default=None, description="Latency", examples=[142.7])
    detalhe: str | None = Field(default=None, description="Outcome detail")


class Meta(BaseModel):
    request_id: str
    cache: CacheStatus
    obtido_em: datetime = Field(description="When the data was collected from the sources")
    fontes: list[SourceInfo] = Field(description="Every source involved and its outcome")
    avisos: list[str] = Field(default_factory=list, description="Degraded-mode warnings")


class Aggregate[T: BaseModel](BaseModel):
    """Aggregated result stored in the cache: the data plus where it came from."""

    data: T
    fontes: list[SourceInfo] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)
    obtido_em: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Internal (not exposed in the response): built while a source failed transiently,
    # so it is cached with a short TTL instead of the resource's full TTL.
    degraded: bool = False


# --- CEP (postal code) ----------------------------------------------------------
class Coordinates(BaseModel):
    latitude: float
    longitude: float


class Municipality(BaseModel):
    codigo_ibge: str = Field(examples=["3550308"])
    nome: str = Field(examples=["São Paulo"])
    uf: str | None = Field(default=None, examples=["SP"])
    microrregiao: str | None = None
    mesorregiao: str | None = None
    regiao_imediata: str | None = None
    regiao_intermediaria: str | None = None
    regiao: str | None = Field(default=None, examples=["Sudeste"])


class AddressPartial(BaseModel):
    """What a single CEP source can tell us. Every field is optional."""

    logradouro: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    cidade: str | None = None
    uf: str | None = None
    codigo_ibge: str | None = None
    ddd: str | None = None
    coordenadas: Coordinates | None = None


class Address(AddressPartial):
    cep: str = Field(examples=["01310-100"])
    municipio: Municipality | None = None
    proveniencia: dict[str, str] = Field(
        default_factory=dict,
        description="Field -> source that provided its value",
        examples=[{"logradouro": "brasilapi", "codigo_ibge": "viacep"}],
    )


# --- CNPJ (company registry) ----------------------------------------------------
class Activity(BaseModel):
    codigo: str = Field(description="CNAE code", examples=["6422100"])
    descricao: str


class Partner(BaseModel):
    nome: str
    qualificacao: str | None = None
    data_entrada: date | None = None


class CompanyAddress(BaseModel):
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    municipio: str | None = None
    uf: str | None = None
    cep: str | None = None


class Company(BaseModel):
    cnpj: str = Field(examples=["00.000.000/0001-91"])
    razao_social: str
    nome_fantasia: str | None = None
    situacao_cadastral: str | None = Field(default=None, examples=["ATIVA"])
    data_situacao_cadastral: date | None = None
    data_abertura: date | None = None
    natureza_juridica: str | None = None
    porte: str | None = None
    capital_social: Decimal | None = None
    atividade_principal: Activity | None = None
    atividades_secundarias: list[Activity] = Field(default_factory=list)
    socios: list[Partner] = Field(default_factory=list)
    email: str | None = None
    telefone: str | None = None
    endereco: CompanyAddress = Field(default_factory=CompanyAddress)
    localizacao: Address | None = Field(
        default=None, description="Address enriched through CEP aggregation (coordinates, IBGE)"
    )
    fonte: str = Field(description="Source that provided the registry data", examples=["brasilapi"])


# --- Response envelopes ---------------------------------------------------------
class CepResponse(BaseModel):
    data: Address
    meta: Meta


class CompanyResponse(BaseModel):
    data: Company
    meta: Meta
