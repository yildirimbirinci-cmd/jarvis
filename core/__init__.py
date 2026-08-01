"""Core runtime services for Artmach Assistant.

The package intentionally avoids eager imports. Core modules have optional
runtime dependencies (for example GUI, audio, and indexing services), so
loading :mod:`artmach_assistant.core` must remain lightweight and side-effect
free. Attribute access to a concrete submodule is resolved lazily.
"""

from __future__ import annotations

import importlib
from types import ModuleType

__all__: list[str] = []


def __getattr__(name: str) -> ModuleType:
    """Lazily expose existing core submodules as package attributes.

    This supports standard imports such as ``from artmach_assistant.core import
    voice_service`` without eagerly importing every optional core service.
    Missing names still raise :class:`AttributeError` as normal.
    """
    if not name or name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    try:
        module = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{__name__}.{name}":
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            ) from None
        raise

    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted(globals())
