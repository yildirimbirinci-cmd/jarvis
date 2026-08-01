from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from artmach_assistant.core.support_bundle import (
    MAX_FILE_BYTES,
    create_support_bundle,
)


def test_support_bundle_contains_only_bounded_diagnostics(tmp_path: Path) -> None:
    data = tmp_path / "data"
    acceptance = data / "logs" / "acceptance" / "latest.json"
    runtime = data / "logs" / "runtime_state.json"
    notification = data / "ui" / "notifications.json"
    crash = data / "logs" / "crashes" / "crash_1.json"
    for path, content in (
        (acceptance, '{"ok": true}'),
        (runtime, '{"status": "stopped"}'),
        (notification, "[]"),
        (crash, '{"message": "test"}'),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (data / "config.json").write_text('{"secret": true}', encoding="utf-8")

    bundle = create_support_bundle(data, tmp_path / "support.zip")

    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "config.json" not in names
        assert "logs/acceptance/latest.json" in names
        manifest = json.loads(archive.read("manifest.json"))
        entry = next(
            item
            for item in manifest["files"]
            if item["path"] == "logs/acceptance/latest.json"
        )
        assert entry["sha256"] == hashlib.sha256(
            acceptance.read_bytes()
        ).hexdigest()


def test_support_bundle_skips_oversized_and_symlinked_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    runtime = data / "logs" / "runtime_state.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    outside = tmp_path / "outside.json"
    outside.write_text("secret", encoding="utf-8")
    notifications = data / "ui" / "notifications.json"
    notifications.parent.mkdir(parents=True)
    try:
        notifications.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    bundle = create_support_bundle(data, tmp_path / "support.zip")

    with zipfile.ZipFile(bundle) as archive:
        assert archive.namelist() == ["manifest.json"]


def test_support_bundle_default_destination_is_under_data_root(
    tmp_path: Path,
) -> None:
    bundle = create_support_bundle(tmp_path / "data")

    assert bundle.parent == (tmp_path / "data" / "support").resolve()
    assert bundle.name.startswith("artmach_support_")


def test_support_bundle_keeps_tail_of_large_voice_log(tmp_path: Path) -> None:
    data = tmp_path / "data"
    voice_log = data / "logs" / "voice_runtime.log"
    voice_log.parent.mkdir(parents=True)
    voice_log.write_bytes(b"old-line\n" + b"x" * MAX_FILE_BYTES + b"\nlatest-command\n")

    bundle = create_support_bundle(data, tmp_path / "support.zip")

    with zipfile.ZipFile(bundle) as archive:
        captured = archive.read("logs/voice_runtime.log")
        manifest = json.loads(archive.read("manifest.json"))
    assert b"latest-command" in captured
    entry = next(
        item for item in manifest["files"]
        if item["path"] == "logs/voice_runtime.log"
    )
    assert entry["truncated"] is True
    assert entry["original_size"] > entry["size"]
