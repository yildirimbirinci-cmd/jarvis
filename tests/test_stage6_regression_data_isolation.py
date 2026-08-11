from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine


def test_full_regression_uses_isolated_appdata(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)

    engine = object.__new__(AssistantEngine)
    engine.own_project_root = lambda: root

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(assistant_module.subprocess, "run", fake_run)

    live_local = os.environ.get("LOCALAPPDATA")
    live_roaming = os.environ.get("APPDATA")

    ok, output = engine._run_own_tests()

    assert ok is True
    assert output == "ok"
    env = captured["env"]
    assert env["JARVIS_REGRESSION_ISOLATED_DATA"] == "1"
    assert env["LOCALAPPDATA"] != live_local
    assert env["APPDATA"] != live_roaming
    assert "jarvis-regression-data-" in env["LOCALAPPDATA"]
    assert captured["cwd"] == str(root)


def test_regression_isolation_is_applied_before_pytest_subprocess():
    source = Path("core/assistant.py").read_text(encoding="utf-8")
    start = source.index("def _run_own_tests(")
    end = source.index("def _runtime_health_check(", start)
    block = source[start:end]

    assert 'test_env["LOCALAPPDATA"]' in block
    assert 'test_env["APPDATA"]' in block
    assert 'test_env["JARVIS_REGRESSION_ISOLATED_DATA"] = "1"' in block
    assert "env=test_env" in block
