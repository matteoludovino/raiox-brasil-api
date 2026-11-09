"""Local CEP and CNPJ validation, performed before any upstream call (saves quota).

CNPJ uses the Brazilian Federal Revenue modulo-11 algorithm, already in the new
**alphanumeric** format (IN RFB 2.229/2024, effective July 2026): the first 12 positions
accept [0-9A-Z] and each character is worth ``ord(c) - 48``. For digit-only CNPJs the result
is identical to the classic algorithm.

Error messages are returned to API clients, so they stay in Portuguese.
"""

from __future__ import annotations

import re

from raiox.core.errors import InvalidInputError

_CEP_RE = re.compile(r"^\d{8}$")
_CNPJ_RE = re.compile(r"^[0-9A-Z]{12}\d{2}$")
_CNPJ_STRIP = re.compile(r"[.\-/\s]")

_DV1_WEIGHTS = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_DV2_WEIGHTS = (6, *_DV1_WEIGHTS)


def normalize_cep(raw: str) -> str:
    cep = raw.strip().replace("-", "").replace(".", "")
    if not _CEP_RE.match(cep) or cep == "00000000":
        raise InvalidInputError(f"CEP inválido: {raw!r}. Use 8 dígitos, ex.: 01310-100")
    return cep


def format_cep(cep: str) -> str:
    return f"{cep[:5]}-{cep[5:]}"


def _check_digit(base: str, weights: tuple[int, ...]) -> int:
    remainder = sum((ord(c) - 48) * w for c, w in zip(base, weights, strict=True)) % 11
    return 0 if remainder < 2 else 11 - remainder


def is_valid_cnpj(cnpj: str) -> bool:
    if not _CNPJ_RE.match(cnpj) or len(set(cnpj)) == 1:
        return False
    dv1 = _check_digit(cnpj[:12], _DV1_WEIGHTS)
    dv2 = _check_digit(cnpj[:12] + str(dv1), _DV2_WEIGHTS)
    return cnpj[12:] == f"{dv1}{dv2}"


def normalize_cnpj(raw: str) -> str:
    cnpj = _CNPJ_STRIP.sub("", raw).upper()
    if not is_valid_cnpj(cnpj):
        raise InvalidInputError(
            f"CNPJ inválido: {raw!r}. Verifique o formato e os dígitos verificadores."
        )
    return cnpj


def is_alphanumeric_cnpj(cnpj: str) -> bool:
    return not cnpj[:12].isdigit()


def format_cnpj(cnpj: str) -> str:
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
