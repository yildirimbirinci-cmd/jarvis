"""Small, dependency-free validators shared by read-only query services."""
from __future__ import annotations

import math

MAX_QUERY_CHARS = 16_384


def normalized_query(value: object, *, maximum: int = MAX_QUERY_CHARS) -> str:
    """Return bounded stripped query text, or an empty string for invalid input."""
    safe_maximum = _positive_int(maximum, name="maximum")
    if not isinstance(value, str) or "\x00" in value:
        return ""
    text = value.strip()
    if not text:
        return ""
    return text[:safe_maximum]


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def bounded_positive_int(value: object, *, default: int, maximum: int) -> int:
    """Coerce a finite positive integer and clamp it to ``maximum``."""
    safe_default = _positive_int(default, name="default")
    safe_maximum = _positive_int(maximum, name="maximum")
    safe_default = min(safe_default, safe_maximum)
    if isinstance(value, bool):
        return safe_default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return safe_default
    if not math.isfinite(number):
        return safe_default
    integer = int(number)
    if integer <= 0:
        return 1
    return min(integer, safe_maximum)
