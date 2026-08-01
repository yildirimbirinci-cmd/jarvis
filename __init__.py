"""Artmach Assistant package root."""
from __future__ import annotations

import sys

__version__ = "0.17.2"

# The historical test suite imports both ``indexing`` and
# ``artmach_assistant.indexing``. Reuse an existing module instead of loading
# the same package twice under different names.
_existing_indexing = sys.modules.get("indexing")
if _existing_indexing is not None:
    sys.modules.setdefault(__name__ + ".indexing", _existing_indexing)
