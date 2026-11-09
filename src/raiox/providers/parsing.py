"""Lenient helpers to normalize heterogeneous source payloads."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from raiox.domain.models import Coordinates


def text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def digits(value: Any) -> str | None:
    s = re.sub(r"\D", "", str(value)) if value is not None else ""
    return s or None


def parse_date(value: Any) -> date | None:
    s = text(value)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def coordinates(lat: Any, lon: Any) -> Coordinates | None:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= la <= 90 and -180 <= lo <= 180) or (la == 0 and lo == 0):
        return None
    return Coordinates(latitude=la, longitude=lo)


def cnae(value: Any) -> str | None:
    d = digits(value)
    if not d or set(d) == {"0"}:
        return None
    return d.zfill(7)
