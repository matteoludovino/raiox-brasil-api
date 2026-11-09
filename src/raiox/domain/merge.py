"""Field-by-field merge of partial responses, with precedence and provenance."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any

from raiox.domain.models import AddressPartial

CEP_FIELDS: tuple[str, ...] = tuple(AddressPartial.model_fields)
# Fields where disagreement between sources signals suspicious data and deserves a warning.
CRITICAL_FIELDS: tuple[str, ...] = ("cidade", "uf")


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _canonical(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()


def merge_cep(
    parts: Sequence[tuple[str, AddressPartial]],
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Merge parts in the order received (= precedence order).

    Returns (fields, provenance, warnings). The first non-empty value of each field wins;
    disagreements on critical fields produce a warning instead of being silently dropped.
    """
    fields: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    warnings: list[str] = []

    for field in CEP_FIELDS:
        values = [
            (source, getattr(p, field)) for source, p in parts if not _is_empty(getattr(p, field))
        ]
        if not values:
            continue
        source, value = values[0]
        fields[field] = value
        provenance[field] = source
        if field in CRITICAL_FIELDS:
            distinct = {_canonical(v) for _, v in values}
            if len(distinct) > 1:
                details = ", ".join(f"{s}={v!r}" for s, v in values)
                # Warning text is part of the API response (Portuguese).
                warnings.append(
                    f"Divergência em '{field}' entre fontes ({details}); usado {source}."
                )

    return fields, provenance, warnings
