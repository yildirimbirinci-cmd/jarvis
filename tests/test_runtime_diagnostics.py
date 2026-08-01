from __future__ import annotations

from pathlib import Path

from artmach_assistant.core import runtime_diagnostics
from artmach_assistant.core.runtime_diagnostics import inspect_runtime


def _package(tmp_path: Path) -> Path:
    package = tmp_path / "artmach_assistant"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "app.py").write_text("", encoding="utf-8")
    (package / "core" / "constitution").mkdir(parents=True)
    return package


def test_runtime_report_exposes_stable_serializable_checks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = _package(tmp_path)
    monkeypatch.setattr(
        "artmach_assistant.core.runtime_diagnostics.importlib.util.find_spec",
        lambda name: object(),
    )

    report = inspect_runtime(package, require_pytest=True)

    assert report.ok
    payload = report.to_dict()
    assert payload["ok"] is True
    assert [item["name"] for item in payload["checks"]] == [
        "python",
        "package",
        "constitution",
        "desktop_ui",
        "test_runtime",
    ]


def test_runtime_report_fails_closed_for_missing_desktop_dependency(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = _package(tmp_path)
    monkeypatch.setattr(
        "artmach_assistant.core.runtime_diagnostics.importlib.util.find_spec",
        lambda name: None if name == "PySide6" else object(),
    )

    report = inspect_runtime(package)

    assert not report.ok
    desktop = next(check for check in report.checks if check.name == "desktop_ui")
    assert not desktop.ok
    assert "kurulu değil" in desktop.detail


def test_runtime_report_rejects_invalid_package_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "artmach_assistant.core.runtime_diagnostics.importlib.util.find_spec",
        lambda name: object(),
    )

    report = inspect_runtime(tmp_path)

    assert not report.ok
    assert next(check for check in report.checks if check.name == "package").ok is False
    assert next(check for check in report.checks if check.name == "constitution").ok is False


def test_voice_acceptance_checks_real_dependencies_and_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = _package(tmp_path)
    monkeypatch.setattr(
        "artmach_assistant.core.runtime_diagnostics.importlib.util.find_spec",
        lambda name: object(),
    )
    monkeypatch.setattr(
        runtime_diagnostics,
        "_microphone_check",
        lambda: runtime_diagnostics.RuntimeCheck(
            "microphone", True, "1 giriş aygıtı kullanılabilir."
        ),
    )
    model_dir = package / "models" / "piper"
    model_dir.mkdir(parents=True)
    (model_dir / "tr_TR-test.onnx").write_bytes(b"model")

    report = inspect_runtime(package, require_voice=True)

    assert report.ok
    names = [check.name for check in report.checks]
    assert names[-5:] == [
        "voice_numpy",
        "voice_audio",
        "voice_stt",
        "microphone",
        "piper_voice",
    ]
