"""Safe, bounded source-file path resolution and text loading."""
from __future__ import annotations

import os
from pathlib import Path


class SourceFileError(ValueError):
    """Raised when a source file cannot be safely read."""


def _validated_path_value(value: str | Path, *, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise SourceFileError(f"{label} must be a string or pathlib.Path")
    raw = str(value).strip()
    if not raw:
        raise SourceFileError(f"{label} cannot be empty")
    if "\x00" in raw:
        raise SourceFileError(f"{label} contains a null byte")
    return Path(raw).expanduser()


def project_file(root: str | Path, value: str | Path, *, must_exist: bool = True) -> Path:
    """Resolve a project-scoped path and reject traversal outside the project root."""
    if not isinstance(must_exist, bool):
        raise SourceFileError("must_exist must be a boolean")
    try:
        project_root = _validated_path_value(root, label="Project root").resolve(strict=False)
        if not project_root.is_dir():
            raise SourceFileError(f"Project root is not a directory: {root}")
        candidate = _validated_path_value(value, label="Source path")
        target = candidate.resolve(strict=False) if candidate.is_absolute() else (project_root / candidate).resolve(strict=False)
        target.relative_to(project_root)
    except SourceFileError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise SourceFileError("Source path cannot be resolved safely") from exc
    if must_exist and not target.is_file():
        raise SourceFileError(f"Source file not found: {value}")
    if not must_exist and target.exists() and not target.is_file():
        raise SourceFileError(f"Source path is not a file: {value}")
    return target


def read_source_text(root: str | Path, value: str | Path, *, max_bytes: int = 2_000_000, max_chars: int | None = None) -> str:
    """Read bounded UTF-8 source text from inside a project root."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise SourceFileError("max_bytes must be a positive integer")
    if max_chars is not None and (isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 0):
        raise SourceFileError("max_chars must be a non-negative integer")
    target = project_file(root, value, must_exist=True)
    if max_chars == 0:
        return ""
    try:
        before = target.stat()
        if not target.is_file():
            raise SourceFileError(f"Source file not found: {value}")
        with target.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
            opened = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise SourceFileError(f"Source file changed while being opened: {value}")
    except SourceFileError:
        raise
    except OSError as exc:
        raise SourceFileError(f"Source file cannot be read: {value}") from exc
    if len(raw) > max_bytes:
        raise SourceFileError(f"Source file exceeds {max_bytes} bytes: {value}")
    if b"\x00" in raw:
        raise SourceFileError(f"Binary file cannot be read as source text: {value}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceFileError(f"Source file is not valid UTF-8: {value}") from exc
    return text if max_chars is None else text[:max_chars]
