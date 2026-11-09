"""CNPJ adapters: BrasilAPI, CNPJ.ws (public API) and ReceitaWS.

The three payload formats are quite different; all of them converge into ``Company`` (without
``localizacao``, which the service fills in through CEP aggregation).
"""

from __future__ import annotations

from typing import Any

from raiox.domain.models import Activity, Company, CompanyAddress, Partner
from raiox.domain.validators import format_cep, format_cnpj
from raiox.providers.base import BaseProvider, ProviderBadResponse, ProviderNotFound
from raiox.providers.parsing import cnae, digits, parse_date, parse_decimal, text


def _cep(value: Any) -> str | None:
    d = digits(value)
    return format_cep(d) if d and len(d) == 8 else None


def _activity(code: Any, description: Any) -> Activity | None:
    cod, desc = cnae(code), text(description)
    return Activity(codigo=cod, descricao=desc) if cod and desc else None


def _activities(items: list[tuple[Any, Any]]) -> list[Activity]:
    return [a for c, d in items if (a := _activity(c, d))]


def _phone(*parts: Any) -> str | None:
    return digits("".join(str(p) for p in parts if p)) or None


class BrasilApiCnpj(BaseProvider):
    name = "brasilapi"

    async def fetch(self, cnpj: str) -> Company:
        d = await self._get_json(f"/cnpj/v1/{cnpj}")
        if not isinstance(d, dict) or not d.get("razao_social"):
            raise ProviderBadResponse("brasilapi: formato inesperado")
        return Company(
            cnpj=format_cnpj(cnpj),
            razao_social=d["razao_social"],
            nome_fantasia=text(d.get("nome_fantasia")),
            situacao_cadastral=text(d.get("descricao_situacao_cadastral")),
            data_situacao_cadastral=parse_date(d.get("data_situacao_cadastral")),
            data_abertura=parse_date(d.get("data_inicio_atividade")),
            natureza_juridica=text(d.get("natureza_juridica")),
            porte=text(d.get("porte")),
            capital_social=parse_decimal(d.get("capital_social")),
            atividade_principal=_activity(d.get("cnae_fiscal"), d.get("cnae_fiscal_descricao")),
            atividades_secundarias=_activities(
                [(a.get("codigo"), a.get("descricao")) for a in d.get("cnaes_secundarios") or []]
            ),
            socios=[
                Partner(
                    nome=s["nome_socio"],
                    qualificacao=text(s.get("qualificacao_socio")),
                    data_entrada=parse_date(s.get("data_entrada_sociedade")),
                )
                for s in d.get("qsa") or []
                if s.get("nome_socio")
            ],
            email=text(d.get("email")),
            telefone=_phone(d.get("ddd_telefone_1")),
            endereco=CompanyAddress(
                logradouro=text(
                    " ".join(
                        filter(None, [d.get("descricao_tipo_de_logradouro"), d.get("logradouro")])
                    )
                ),
                numero=text(d.get("numero")),
                complemento=text(d.get("complemento")),
                bairro=text(d.get("bairro")),
                municipio=text(d.get("municipio")),
                uf=text(d.get("uf")),
                cep=_cep(d.get("cep")),
            ),
            fonte=self.name,
        )


class CnpjWs(BaseProvider):
    name = "cnpjws"

    async def fetch(self, cnpj: str) -> Company:
        d = await self._get_json(f"/cnpj/{cnpj}")
        if not isinstance(d, dict) or not d.get("razao_social"):
            raise ProviderBadResponse("cnpjws: formato inesperado")
        est = d.get("estabelecimento") or {}
        main_activity = est.get("atividade_principal") or {}
        return Company(
            cnpj=format_cnpj(cnpj),
            razao_social=d["razao_social"],
            nome_fantasia=text(est.get("nome_fantasia")),
            situacao_cadastral=(text(est.get("situacao_cadastral")) or "").upper() or None,
            data_situacao_cadastral=parse_date(est.get("data_situacao_cadastral")),
            data_abertura=parse_date(est.get("data_inicio_atividade")),
            natureza_juridica=text((d.get("natureza_juridica") or {}).get("descricao")),
            porte=text((d.get("porte") or {}).get("descricao")),
            capital_social=parse_decimal(d.get("capital_social")),
            atividade_principal=_activity(main_activity.get("id"), main_activity.get("descricao")),
            atividades_secundarias=_activities(
                [(a.get("id"), a.get("descricao")) for a in est.get("atividades_secundarias") or []]
            ),
            socios=[
                Partner(
                    nome=s["nome"],
                    qualificacao=text((s.get("qualificacao_socio") or {}).get("descricao")),
                    data_entrada=parse_date(s.get("data_entrada")),
                )
                for s in d.get("socios") or []
                if s.get("nome")
            ],
            email=text(est.get("email")),
            telefone=_phone(est.get("ddd1"), est.get("telefone1")),
            endereco=CompanyAddress(
                logradouro=text(
                    " ".join(filter(None, [est.get("tipo_logradouro"), est.get("logradouro")]))
                ),
                numero=text(est.get("numero")),
                complemento=text(est.get("complemento")),
                bairro=text(est.get("bairro")),
                municipio=text((est.get("cidade") or {}).get("nome")),
                uf=text((est.get("estado") or {}).get("sigla")),
                cep=_cep(est.get("cep")),
            ),
            fonte=self.name,
        )


class ReceitaWs(BaseProvider):
    name = "receitaws"

    async def fetch(self, cnpj: str) -> Company:
        d = await self._get_json(f"/cnpj/{cnpj}")
        if not isinstance(d, dict):
            raise ProviderBadResponse("receitaws: formato inesperado")
        if d.get("status") == "ERROR":  # answers 200 with status ERROR
            raise ProviderNotFound(f"receitaws: {d.get('message', 'não encontrado')}")
        if not d.get("nome"):
            raise ProviderBadResponse("receitaws: formato inesperado")
        main_activity = (d.get("atividade_principal") or [{}])[0]
        return Company(
            cnpj=format_cnpj(cnpj),
            razao_social=d["nome"],
            nome_fantasia=text(d.get("fantasia")),
            situacao_cadastral=text(d.get("situacao")),
            data_situacao_cadastral=parse_date(d.get("data_situacao")),
            data_abertura=parse_date(d.get("abertura")),
            natureza_juridica=text(d.get("natureza_juridica")),
            porte=text(d.get("porte")),
            capital_social=parse_decimal(d.get("capital_social")),
            atividade_principal=_activity(main_activity.get("code"), main_activity.get("text")),
            atividades_secundarias=_activities(
                [(a.get("code"), a.get("text")) for a in d.get("atividades_secundarias") or []]
            ),
            socios=[
                Partner(nome=s["nome"], qualificacao=text(s.get("qual")))
                for s in d.get("qsa") or []
                if s.get("nome")
            ],
            email=text(d.get("email")),
            telefone=_phone(d.get("telefone")),
            endereco=CompanyAddress(
                logradouro=text(d.get("logradouro")),
                numero=text(d.get("numero")),
                complemento=text(d.get("complemento")),
                bairro=text(d.get("bairro")),
                municipio=text(d.get("municipio")),
                uf=text(d.get("uf")),
                cep=_cep(d.get("cep")),
            ),
            fonte=self.name,
        )
