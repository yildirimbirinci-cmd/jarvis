from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping


def write_acceptance_report(
    path: str | Path,
    payload: Mapping[str, object],
) -> Path:
    """Persist the latest acceptance result atomically."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
