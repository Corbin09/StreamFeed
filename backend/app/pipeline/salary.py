"""Parse free-text salaries written in Vietnamese or English.

Examples:
    "15 - 25 triệu"        -> 15,000,000 - 25,000,000 VND
    "Up To 35M Gross"      -> max 35,000,000 VND
    "1,000 - 1,750 USD"    -> 1000 - 1750 USD
    "1.500 - 2.000 USD"    -> 1500 - 2000 USD ("." is the VN thousands separator)
    "$1.5k"                -> 1500 USD
    "Thỏa thuận"           -> negotiable
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.pipeline.text import fold

NEGOTIABLE = (
    "thoa thuan",
    "negotiable",
    "canh tranh",
    "competitive",
    "you'll love it",
    "you will love it",
    "hap dan",
    "attractive",
    "upon interview",
    "theo nang luc",
)
# Values at or above this are VND even if the source labels them USD (ITviec does).
VND_THRESHOLD = Decimal(100_000)

_NUMBER = r"(\d+(?:[.,]\d+)*)"
_UNIT = r"(trieu|tr|million|mil|m|k|ty)?"
_AMOUNT = re.compile(rf"(\$\s*)?{_NUMBER}\s*{_UNIT}(?![a-z])")
_MAX_ONLY = re.compile(
    r"\b(up ?to|upto|toi da|toi|len den|den|max(?:imum)?|under|duoi)\b\s*\$?\s*$"
)
_MIN_ONLY = re.compile(r"\b(from|tu|min(?:imum)?|tren|above|over|at least|it nhat)\b\s*\$?\s*$")
_MULTIPLIER = {
    "trieu": Decimal(1_000_000),
    "tr": Decimal(1_000_000),
    "million": Decimal(1_000_000),
    "mil": Decimal(1_000_000),
    "m": Decimal(1_000_000),
    "k": Decimal(1_000),
    "ty": Decimal(1_000_000_000),
}


@dataclass(frozen=True)
class Salary:
    min: Decimal | None
    max: Decimal | None
    currency: str | None
    negotiable: bool = False


def _to_decimal(token: str) -> Decimal:
    """'1,000' / '1.500' -> thousands separators; '1.5' / '2,5' -> decimals."""
    groups = re.split(r"[.,]", token)
    if len(groups) > 1 and all(len(g) == 3 for g in groups[1:]):
        return Decimal("".join(groups))
    if len(groups) == 2:
        return Decimal(f"{groups[0]}.{groups[1]}")
    return Decimal("".join(groups))


def parse_salary(text: str | None, currency_hint: str | None = None) -> Salary | None:
    if not text:
        return None
    s = fold(text)
    amounts = list(_AMOUNT.finditer(s))
    if not amounts:
        return (
            Salary(None, None, None, negotiable=True) if any(k in s for k in NEGOTIABLE) else None
        )

    amounts = amounts[:2]
    units = [m.group(3) for m in amounts]
    # "15 - 25 trieu": the unit written once applies to the whole range.
    shared_unit = next((u for u in reversed(units) if u), None)
    values = []
    for match, unit in zip(amounts, units, strict=True):
        value = _to_decimal(match.group(2)) * _MULTIPLIER.get(unit or shared_unit or "", Decimal(1))
        values.append(value)

    explicit_usd = "$" in s or "usd" in s
    explicit_vnd = "vnd" in s or "dong" in s or (shared_unit in {"trieu", "tr", "ty"})
    largest = max(values)
    if largest >= VND_THRESHOLD or explicit_vnd:
        currency = "VND"
    elif explicit_usd:
        currency = "USD"
    elif currency_hint:
        currency = currency_hint.upper()
    else:
        currency = "USD" if largest < VND_THRESHOLD else "VND"

    if len(values) == 2:
        low, high = sorted(values)
        return Salary(low, high, currency)
    before = s[: amounts[0].start()]
    if _MAX_ONLY.search(before):
        return Salary(None, values[0], currency)
    if _MIN_ONLY.search(before):
        return Salary(values[0], None, currency)
    return Salary(values[0], values[0], currency)
