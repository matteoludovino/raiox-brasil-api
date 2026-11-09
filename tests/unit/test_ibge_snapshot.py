from raiox.domain.models import Municipality
from raiox.providers.ibge_snapshot import IbgeSnapshot, encode


def test_bundled_snapshot_covers_brazil() -> None:
    snap = IbgeSnapshot()
    sp = snap.lookup("3550308")
    assert sp is not None
    assert (sp.nome, sp.uf, sp.regiao) == ("São Paulo", "SP", "Sudeste")
    assert snap.lookup("0000000") is None
    assert len(snap._snapshot["data"]) > 5500
    assert snap.generated_at


def test_encode_roundtrip() -> None:
    m = Municipality(codigo_ibge="1", nome="X", uf="AA", regiao="Norte")
    snap = IbgeSnapshot(encode([m], generated_at="2026-01-01"))
    assert snap.lookup("1") == m
    assert snap.generated_at == "2026-01-01"
