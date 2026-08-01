"""Reuse a recent baseline test only for the exact same source tree."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import time

from artmach_assistant.core.store_validation import atomic_write_json, read_json_object


CACHE_TTL_SECONDS = 10 * 60
MAX_CACHE_BYTES = 32 * 1024
_IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".artmach_assistant"}
_SOURCE_SUFFIXES = {".py", ".json", ".toml", ".ini", ".cfg", ".txt"}


@dataclass(frozen=True, slots=True)
class BaselineCache:
    success: bool
    output: str
    fingerprint: str
    created_at: float


def source_tree_fingerprint(root: Path) -> str:
    root = Path(root).resolve(strict=True)
    digest = hashlib.sha256()
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(
            name for name in names
            if name.casefold() not in _IGNORED_DIRS
            and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or path.suffix.casefold() not in _SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
    return digest.hexdigest()


def save_baseline_cache(
    cache_path: Path,
    root: Path,
    success: bool,
    output: str,
    *,
    now: float | None = None,
) -> None:
    atomic_write_json(cache_path, {
        "version": 1,
        "created_at": time.time() if now is None else float(now),
        "fingerprint": source_tree_fingerprint(root),
        "success": bool(success),
        "output": str(output)[-20000:],
    }, max_bytes=MAX_CACHE_BYTES)


def load_baseline_cache(
    cache_path: Path,
    root: Path,
    *,
    now: float | None = None,
) -> BaselineCache | None:
    current = time.time() if now is None else float(now)
    try:
        data = read_json_object(cache_path, max_bytes=MAX_CACHE_BYTES)
        if not isinstance(data, dict) or data.get("version") != 1:
            return None
        created = float(data["created_at"])
        fingerprint = str(data["fingerprint"])
        success = data["success"]
        output = data["output"]
        if type(success) is not bool or not isinstance(output, str):
            return None
        if current < created or current - created > CACHE_TTL_SECONDS:
            return None
        if fingerprint != source_tree_fingerprint(root):
            return None
        return BaselineCache(success, output, fingerprint, created)
    except (OSError, KeyError, TypeError, ValueError, OverflowError):
        return None
