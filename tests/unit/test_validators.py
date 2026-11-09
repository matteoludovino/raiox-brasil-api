import pytest

from raiox.core.errors import InvalidInputError
from raiox.domain.validators import (
    format_cep,
    format_cnpj,
    is_alphanumeric_cnpj,
    is_valid_cnpj,
    normalize_cep,
    normalize_cnpj,
)


@pytest.mark.parametrize("raw", ["01310100", "01310-100", " 01310-100 ", "01.310-100"])
def test_normalize_cep_accepts_formats(raw: str) -> None:
    assert normalize_cep(raw) == "01310100"


@pytest.mark.parametrize("raw", ["", "1234567", "123456789", "ABCDE-123", "00000000"])
def test_normalize_cep_rejects(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        normalize_cep(raw)


def test_format_cep() -> None:
    assert format_cep("01310100") == "01310-100"


@pytest.mark.parametrize(
    "raw",
    [
        "11.222.333/0001-81",
        "11222333000181",
        "00.000.000/0001-91",
        "12.ABC.345/01DE-35",
        "12abc34501de35",
    ],
)
def test_valid_cnpj(raw: str) -> None:
    assert is_valid_cnpj(normalize_cnpj(raw))


@pytest.mark.parametrize(
    "raw",
    [
        "11.222.333/0001-82",  # wrong check digit
        "11111111111111",  # repeated digits
        "1122233300018",  # too short
        "12.ABC.345/01DE-3A",  # check digit cannot be a letter
        "12.ABC.345/01DE-36",  # wrong alphanumeric check digit
    ],
)
def test_invalid_cnpj(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        normalize_cnpj(raw)


def test_alphanumeric_cnpj_detected() -> None:
    assert is_alphanumeric_cnpj("12ABC34501DE35")
    assert not is_alphanumeric_cnpj("11222333000181")


def test_format_cnpj() -> None:
    assert format_cnpj("12ABC34501DE35") == "12.ABC.345/01DE-35"
