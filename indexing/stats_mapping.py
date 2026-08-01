"""Backward-compatible statistics mappings with revision metadata."""
from __future__ import annotations


class RevisionStats(dict[str, int]):
    """Expose revision fields while preserving equality with legacy counters."""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, dict):
            return super().__eq__(other)
        if super().__eq__(other):
            return True
        if any(str(key).endswith("_revision") for key in other):
            return False
        legacy = {
            key: value
            for key, value in self.items()
            if not str(key).endswith("_revision")
        }
        return legacy == other
