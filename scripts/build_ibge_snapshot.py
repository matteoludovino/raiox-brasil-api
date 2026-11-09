"""Builds src/raiox/data/ibge_municipios.json from the IBGE API.

Usage: uv run python scripts/build_ibge_snapshot.py   (or: make snapshot)
Must run from a Brazilian IP: the IBGE API does not answer requests from abroad.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from raiox.providers.cep import parse_municipality
from raiox.providers.ibge_snapshot import SNAPSHOT_FILE, encode

URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
OUTPUT = Path(__file__).resolve().parents[1] / "src" / "raiox" / "data" / SNAPSHOT_FILE
MIN_EXPECTED = 5500  # Brazil has ~5,570 municipalities; fewer means a truncated response


def main() -> int:
    resp = httpx.get(URL, timeout=60)
    resp.raise_for_status()
    municipalities = sorted(
        (parse_municipality(item) for item in resp.json()), key=lambda m: m.codigo_ibge
    )
    if len(municipalities) < MIN_EXPECTED:
        print(f"Only {len(municipalities)} municipalities returned; refusing to write.")
        return 1
    snapshot = encode(municipalities, generated_at=datetime.now(UTC).date().isoformat())
    OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(
        f"Wrote {len(municipalities)} municipalities to {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
