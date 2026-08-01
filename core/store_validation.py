from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key is not allowed: {key!r}")
        result[key] = value
    return result


def _read_json(path: Path, *, max_bytes: int) -> Any:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    with Path(path).open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"JSON payload exceeds {max_bytes} bytes")
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Non-finite JSON number is not allowed: {value}")
        ),
    )


def read_json_object(path: Path, *, max_bytes: int) -> dict[str, Any]:
    payload = _read_json(path, max_bytes=max_bytes)
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")
    return payload


def read_json_array(path: Path, *, max_bytes: int) -> list[Any]:
    payload = _read_json(path, max_bytes=max_bytes)
    if not isinstance(payload, list):
        raise ValueError("JSON payload must be an array")
    return payload


def require_schema_version(payload: dict[str, Any], *, field: str, expected: int) -> None:
    value = payload.get(field)
    if type(value) is not int or value != expected:
        raise ValueError(f"Unsupported {field}: {value!r}")


def dump_json(payload: dict[str, Any], *, max_bytes: int | None = None) -> str:
    if not isinstance(payload, dict):
        raise TypeError("JSON payload must be a dictionary")
    data = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    if max_bytes is not None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        encoded_size = len(data.encode("utf-8"))
        if encoded_size > max_bytes:
            raise ValueError(
                f"JSON payload is too large: {encoded_size} bytes (maximum {max_bytes})"
            )
    return data


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(target: Path, payload: dict[str, Any], *, max_bytes: int | None = None) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = dump_json(payload, max_bytes=max_bytes)
    fd, temp_name = tempfile.mkstemp(
        prefix=target.stem + "-",
        suffix=".tmp",
        dir=str(target.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            written = handle.write(data)
            if written != len(data):
                raise OSError(f"Short JSON write: {written}/{len(data)} characters")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        _fsync_directory(target.parent)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
