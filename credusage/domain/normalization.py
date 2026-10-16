"""Explicit numerical contracts. Never guess a percentage scale."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime


def number(value: object, *, allow_negative: bool = False) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Invalid numeric type")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Malformed number") from None
    if not result.is_finite() or (result < 0 and not allow_negative):
        raise ValueError("Invalid numeric value")
    return result


def percent_used(value: object) -> Decimal | None:
    return number(value)


def fraction_used(value: object) -> Decimal | None:
    result = number(value)
    return result * 100 if result is not None else None


def fraction_remaining(value: object) -> Decimal | None:
    result = number(value)
    if result is None:
        return None
    if result > 1:
        raise ValueError("Remaining fraction exceeds whole")
    return (1 - result) * 100


def count_percentage(used: object, limit: object) -> Decimal | None:
    numerator, denominator = number(used), number(limit)
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


def source_money(value: object, *, scale: Decimal, currency: str) -> Decimal | None:
    if not currency or not scale.is_finite() or scale <= 0:
        raise ValueError("Explicit currency and positive source scale required")
    result = number(value, allow_negative=True)
    return result / scale if result is not None else None


def utc_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Invalid timestamp")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float, Decimal)):
        raw = number(value)
        try:
            parsed = datetime.fromtimestamp(float(raw), timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise ValueError("Invalid timestamp") from None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid timestamp") from None
    else:
        raise ValueError("Invalid timestamp")
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed.astimezone(timezone.utc)


def retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    try:
        seconds = number(value)
        return float(seconds) if seconds is not None else None
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            return max(0.0, (utc_timestamp(target) - now).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None
