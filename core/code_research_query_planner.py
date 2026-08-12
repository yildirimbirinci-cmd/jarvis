from __future__ import annotations

from typing import Iterable

_MAX_QUERY_COUNT = 4


def _clean(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _symbol_tail(symbol: str) -> str:
    return _clean(symbol).rsplit(".", 1)[-1]


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean(value)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        rows.append(cleaned)
        if len(rows) >= _MAX_QUERY_COUNT:
            break
    return tuple(rows)


def plan_external_code_queries(*, title: str, path: str = "", symbol: str = "") -> tuple[str, ...]:
    """Build target-aware code research queries without issue-specific branches."""
    title = _clean(title)
    path = _clean(path)
    symbol = _clean(symbol)
    symbol_tail = _symbol_tail(symbol)

    target = symbol or symbol_tail or path or title
    context = " ".join(part for part in (title, symbol_tail) if part).strip()
    if not context:
        context = target

    return _unique(
        (
            f"Python {context} official documentation",
            f"Python {target} troubleshooting root cause",
            f"{context} Python GitHub issue regression",
            f"Python {context} testing validation",
        )
    )
