from raiox.domain.merge import merge_cep
from raiox.domain.models import AddressPartial, Coordinates


def test_merge_respects_precedence_and_records_provenance() -> None:
    brasilapi = AddressPartial(
        logradouro="Avenida Paulista",
        cidade="São Paulo",
        uf="SP",
        coordenadas=Coordinates(latitude=-23.5, longitude=-46.6),
    )
    viacep = AddressPartial(
        logradouro="Av. Paulista",
        cidade="São Paulo",
        uf="SP",
        codigo_ibge="3550308",
        ddd="11",
        complemento="",
    )
    fields, provenance, warnings = merge_cep([("brasilapi", brasilapi), ("viacep", viacep)])

    assert fields["logradouro"] == "Avenida Paulista"
    assert provenance["logradouro"] == "brasilapi"
    assert fields["codigo_ibge"] == "3550308"
    assert provenance["codigo_ibge"] == "viacep"
    assert "complemento" not in fields  # an empty string does not count as a value
    assert provenance["coordenadas"] == "brasilapi"
    assert warnings == []


def test_critical_disagreement_warns_but_ignores_accents_and_case() -> None:
    a = AddressPartial(cidade="São Paulo", uf="SP")
    b = AddressPartial(cidade="SAO PAULO", uf="SP")
    c = AddressPartial(cidade="Santo André", uf="SP")

    _, _, warnings = merge_cep([("a", a), ("b", b)])
    assert warnings == []

    fields, _, warnings = merge_cep([("a", a), ("c", c)])
    assert fields["cidade"] == "São Paulo"
    assert len(warnings) == 1
    assert "cidade" in warnings[0]
