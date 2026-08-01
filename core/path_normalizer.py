"""Shared, defensive path normalization helpers for project-scoped services."""
from __future__ import annotations

import os
from pathlib import Path

PathLike = str | Path


def _raw_path(value: PathLike, *, label: str = "path") -> str:
    if not isinstance(value, (str, Path)):
        raise TypeError(f"{label} must be a string or pathlib.Path")
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{label} cannot be empty")
    if "\x00" in raw:
        raise ValueError(f"{label} cannot contain NUL characters")
    return raw


def normalize_path(value: PathLike) -> Path:
    """Return an absolute, normalized path or raise for invalid input."""
    return Path(_raw_path(value)).expanduser().resolve(strict=False)


def normalize_project_root(value: PathLike) -> Path:
    """Normalize a project root path."""
    return normalize_path(value)


def project_path(root: PathLike, value: PathLike, *, require_inside: bool = True) -> Path:
    """Resolve *value* against *root* and optionally enforce project containment."""
    if not isinstance(require_inside, bool):
        raise TypeError("require_inside must be a boolean")
    normalized_root = normalize_project_root(root)
    candidate = Path(_raw_path(value)).expanduser()
    if not candidate.is_absolute():
        candidate = normalized_root / candidate
    candidate = candidate.resolve(strict=False)
    if require_inside:
        try:
            candidate.relative_to(normalized_root)
        except ValueError as exc:
            raise ValueError(f"path is outside project root: {candidate}") from exc
    return candidate


def path_key(value: PathLike) -> str:
    """Return a platform-correct canonical key for an absolute path."""
    return os.path.normcase(os.path.normpath(str(normalize_path(value))))


def is_within_root(root: PathLike, value: PathLike) -> bool:
    """Return whether *value* resolves inside *root*."""
    try:
        project_path(root, value, require_inside=True)
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
