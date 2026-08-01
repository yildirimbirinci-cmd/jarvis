from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from artmach_assistant.core.final_release import (
    FinalReleaseError,
    build_final_release,
    install_release,
    iter_release_files,
    run_first_run_checks,
    validate_acceptance_report,
    verify_release_tree,
)


def _project(root: Path) -> Path:
    project = root / "artmach_assistant"
    for directory in ("core", "indexing", "tests", "tools"):
        (project / directory).mkdir(parents=True, exist_ok=True)
    for name in ("app.py", "__main__.py", "config.py"):
        (project / name).write_text("VALUE = 1\n", encoding="utf-8")
    (project / "core" / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
    (project / "tools" / "piper").mkdir()
    (project / "tools" / "piper" / "piper.exe").write_bytes(b"MZ" + b"0" * 100)
    (project / "models" / "piper").mkdir(parents=True)
    (project / "models" / "piper" / "voice.onnx").write_bytes(b"model")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "bad.pyc").write_bytes(b"bad")
    (project / ".venv").mkdir()
    (project / ".venv" / "secret.txt").write_text("omit", encoding="utf-8")
    return project


def _acceptance(path: Path, *, ready: bool = True) -> Path:
    payload = {
        "run_id": "E2E-READY",
        "profile": "full",
        "ready": ready,
        "cancelled": False,
        "checks": [{"name": "all", "required": True, "state": "passed"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_acceptance_must_be_full_and_ready(tmp_path: Path) -> None:
    path = _acceptance(tmp_path / "report.json", ready=False)
    with pytest.raises(FinalReleaseError, match="hazır"):
        validate_acceptance_report(path)


def test_clean_file_selection_excludes_runtime_artifacts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    names = {relative.as_posix() for _path, relative in iter_release_files(project)}
    assert "app.py" in names
    assert "tools/piper/piper.exe" in names
    assert not any("__pycache__" in name for name in names)
    assert not any(".venv" in name for name in names)
    assert not any(name.endswith(".pyc") for name in names)


def test_build_final_release_and_verify_hashes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    report = _acceptance(tmp_path / "report.json")
    result = build_final_release(project, report, tmp_path / "out", version="1.0.0")
    source_zip = Path(result["source_zip"])
    rollback = Path(result["rollback_zip"])
    assert source_zip.is_file() and rollback.is_file()
    with zipfile.ZipFile(source_zip) as archive:
        names = set(archive.namelist())
        assert "artmach_assistant/app.py" in names
        assert "Jarvis_Baslat.cmd" in names
        assert "SHA256SUMS.txt" in names
        assert not any("__pycache__" in name for name in names)
        extract = tmp_path / "release"
        archive.extractall(extract)
    verify_release_tree(extract)


def test_install_release_backs_up_and_replaces_atomically(tmp_path: Path) -> None:
    project = _project(tmp_path / "source")
    report = _acceptance(tmp_path / "report.json")
    result = build_final_release(project, report, tmp_path / "out")
    release_root = tmp_path / "release"
    with zipfile.ZipFile(result["source_zip"]) as archive:
        archive.extractall(release_root)
    destination = tmp_path / "destination"
    old = destination / "artmach_assistant"
    old.mkdir(parents=True)
    (old / "old.txt").write_text("old", encoding="utf-8")
    installed = install_release(release_root, destination)
    assert (destination / "artmach_assistant" / "app.py").is_file()
    assert not (destination / "artmach_assistant" / "old.txt").exists()
    assert installed["backup"]
    with zipfile.ZipFile(installed["backup"]) as archive:
        assert "old.txt" in archive.namelist()


def test_first_run_uses_acceptance_and_required_imports(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    report_path = _acceptance(tmp_path / "report.json")
    monkeypatch.setattr(sys, "version_info", (3, 11, 9, "final", 0))
    import_checker = lambda name: (True, "test")
    report = run_first_run_checks(
        project,
        acceptance_report=report_path,
        import_checker=import_checker,
    )
    assert report.ready
    assert any(item.name == "Tam Windows kabulü" and item.state == "passed" for item in report.checks)


def test_install_release_can_refresh_in_place(tmp_path: Path) -> None:
    project = _project(tmp_path / "source")
    report = _acceptance(tmp_path / "report.json")
    result = build_final_release(project, report, tmp_path / "out")
    release_root = tmp_path / "release"
    with zipfile.ZipFile(result["source_zip"]) as archive:
        archive.extractall(release_root)
    installed = install_release(release_root, release_root)
    assert Path(installed["destination"]).is_dir()
    assert (release_root / "Jarvis_Baslat.cmd").is_file()
    assert installed["backup"]
