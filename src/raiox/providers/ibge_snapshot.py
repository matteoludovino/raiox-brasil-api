"""Local snapshot of IBGE municipalities, used as a fallback when the live API fails.

Municipalities are reference data that changes a few times per decade, so a snapshot is
an accurate fallback. It also matters in practice: the IBGE API does not answer requests
from outside Brazil, so a deployment hosted abroad depends on it. See ADR 0007.

Regenerate with ``make snapshot`` (must run from a Brazilian IP).
"""

from __future__ import annotations

import json
from functools import cached_property
from importlib import resources
from typing import Any

from raiox.domain.models import Municipality

SNAPSHOT_FILE = "ibge_municipios.json"
FIELDS = (
    "nome",
    "uf",
    "microrregiao",
    "mesorregiao",
    "regiao_imediata",
    "regiao_intermediaria",
    "regiao",
)


def encode(municipalities: list[Municipality], *, generated_at: str) -> dict[str, Any]:
    """Compact on-disk format: one row per municipality, columns listed once in ``fields``."""
    return {
        "source": "https://servicodados.ibge.gov.br/api/v1/localidades/municipios",
        "generated_at": generated_at,
        "count": len(municipalities),
        "fields": list(FIELDS),
        "data": {m.codigo_ibge: [getattr(m, f) for f in FIELDS] for m in municipalities},
    }


class IbgeSnapshot:
    name = "ibge:snapshot"

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        self._raw = raw  # injectable for tests; otherwise loaded lazily from package data

    @cached_property
    def _snapshot(self) -> dict[str, Any]:
        if self._raw is not None:
            return self._raw
        text = resources.files("raiox.data").joinpath(SNAPSHOT_FILE).read_text("utf-8")
        data: dict[str, Any] = json.loads(text)
        return data

    @property
    def generated_at(self) -> str:
        return str(self._snapshot["generated_at"])

    def lookup(self, code: str) -> Municipality | None:
        row = self._snapshot["data"].get(code)
        if row is None:
            return None
        return Municipality(
            codigo_ibge=code, **dict(zip(self._snapshot["fields"], row, strict=True))
        )
