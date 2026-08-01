from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024


def create_support_bundle(
    data_root: str | Path,
    destination: str | Path | None = None,
) -> Path:
    root = Path(data_root).expanduser().resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = (
        Path(destination).expanduser().resolve()
        if destination is not None
        else root / "support" / f"artmach_support_{timestamp}.zip"
    )
    target.parent.mkdir(parents=True, exist_ok=True)

    candidates = [
        root / "logs" / "acceptance" / "latest.json",
        root / "logs" / "acceptance" / "e2e_latest.json",
        root / "logs" / "runtime_state.json",
        root / "logs" / "voice_runtime.log",
        root / "logs" / "constitution_runtime.log",
        root / "ui" / "notifications.json",
    ]
    crash_dir = root / "logs" / "crashes"
    if crash_dir.is_dir():
        candidates.extend(sorted(crash_dir.glob("crash_*.json"))[-5:])

    selected: list[tuple[str, bytes, int, str, bool, int]] = []
    total = 0
    for path in candidates:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            resolved.relative_to(root)
            size = resolved.stat().st_size
            truncated = False
            if size > MAX_FILE_BYTES:
                if resolved.suffix.casefold() != ".log":
                    continue
                # Runtime logs are the most valuable diagnostic evidence.
                # Preserve their recent tail rather than omitting the entire
                # file as older support bundles did.
                with resolved.open("rb") as handle:
                    handle.seek(-MAX_FILE_BYTES, os.SEEK_END)
                    content = handle.read(MAX_FILE_BYTES)
                first_newline = content.find(b"\n")
                if first_newline >= 0:
                    content = content[first_newline + 1:]
                truncated = True
            else:
                content = resolved.read_bytes()
            if total + len(content) > MAX_TOTAL_BYTES:
                continue
            relative = resolved.relative_to(root).as_posix()
            selected.append(
                (
                    relative,
                    content,
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                    truncated,
                    size,
                )
            )
            total += len(content)
        except (OSError, ValueError):
            continue

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "files": [
            {
                "path": relative,
                "size": size,
                "sha256": digest,
                "truncated": truncated,
                "original_size": original_size,
            }
            for relative, _content, size, digest, truncated, original_size in selected
        ],
    }

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for relative, content, _size, _digest, _truncated, _original_size in selected:
                archive.writestr(relative, content)
            archive.writestr(
                "manifest.json",
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                + "\n",
            )
        # Windows rejects fsync on a read-only descriptor with WinError 9.
        # Reopen the completed archive as read/write before flushing it.
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
