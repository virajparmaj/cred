"""Shared display formatting; source values retain their original precision."""

from decimal import ROUND_HALF_UP, Decimal


def percentage(value: Decimal) -> str:
    return format(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP), ".0f")
