from __future__ import annotations

from pathlib import Path


def test_app_starts_and_stops_self_improvement_lifecycle() -> None:
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "SelfImprovementApplicationLifecycle.create_default" in source
    assert "self_improvement_lifecycle.start()" in source
    assert "app.aboutToQuit.connect(self_improvement_lifecycle.stop)" in source
    assert "self_improvement_lifecycle.stop()" in source


def test_module_cli_exposes_runtime_status() -> None:
    source = (Path(__file__).resolve().parents[1] / "__main__.py").read_text(encoding="utf-8")
    assert '"--self-improvement-status"' in source
    assert "SelfImprovementApplicationLifecycle.read_status" in source
