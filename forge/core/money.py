"""Money handling.

Every amount that touches the ledger is an integer number of US cents.
Floats are allowed only inside a venture's own head (e.g. a SOL position
size); the moment a number becomes a claim about profit or loss it is
converted here and rounded, once, deliberately.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def usd(amount: str | int | float | Decimal) -> int:
    """Convert a dollar amount to integer cents."""
    return int(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


def cents_to_str(cents: int) -> str:
    """Render cents as a signed dollar string, e.g. -1234 -> '-$12.34'."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def pct(numerator: float, denominator: float) -> float:
    """Percentage, defined as 0.0 when the denominator is 0."""
    if not denominator:
        return 0.0
    return 100.0 * numerator / denominator
