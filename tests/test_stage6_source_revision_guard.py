from __future__ import annotations

import subprocess
from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine


def test_cycle_source_revision_contract_is_installed() -> None:
    source = Path("core/assistant.py").read_text(encoding="utf-8")
    assert "def _current_own_code_revision(" in source
    assert '"source_revision": str(source_revision or "")[:128]' in source
    assert 'previous.get("source_revision", "")' in source


def test_recovery_rejects_changed_committed_revision_before_git_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    monkeypatch.setattr(engine, "own_project_root", lambda: tmp_path)
    monkeypatch.setattr(engine, "_current_own_code_revision", lambda root=None: "revision-new")

    def forbidden_run(*args, **kwargs):
        raise AssertionError("git status must not run after revision mismatch")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    ok, detail = engine._verify_interrupted_engineering_recovery(
        {
            "source_revision": "revision-old",
            "changed_paths": [],
            "failures": [],
            "attempt": 1,
        }
    )
    assert ok is False
    assert "source revision changed" in detail.lower()
    assert "stale recovery evidence" in detail.lower()


def test_recovery_rejects_revision_change_after_validation(monkeypatch) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    revisions = iter(("revision-a", "revision-b"))
    monkeypatch.setattr(engine, "_current_own_code_revision", lambda root=None: next(revisions))
    monkeypatch.setattr(engine, "_compile_own_code", lambda: (True, "compile ok"))
    monkeypatch.setattr(engine, "_runtime_health_check", lambda: (True, "runtime ok"))
    monkeypatch.setattr(engine, "_run_own_tests", lambda: (True, "1 passed"))
    monkeypatch.setattr(engine, "_save_own_code_cycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_save_own_validation", lambda *args, **kwargs: None)

    ok, detail = engine._validate_recovered_engineering_source(
        {
            "source_revision": "revision-a",
            "changed_paths": [],
            "failures": [],
            "attempt": 1,
            "detail": "recovery",
        }
    )
    assert ok is False
    assert "source revision changed while verification was running" in detail.lower()


def test_legacy_cycle_without_source_revision_keeps_existing_behavior(monkeypatch) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    monkeypatch.setattr(engine, "_current_own_code_revision", lambda root=None: "revision-current")
    monkeypatch.setattr(engine, "_compile_own_code", lambda: (True, "compile ok"))
    monkeypatch.setattr(engine, "_runtime_health_check", lambda: (True, "runtime ok"))
    monkeypatch.setattr(engine, "_run_own_tests", lambda: (True, "1 passed"))
    monkeypatch.setattr(engine, "_save_own_code_cycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_save_own_validation", lambda *args, **kwargs: None)

    ok, detail = engine._validate_recovered_engineering_source(
        {
            "changed_paths": [],
            "failures": [],
            "attempt": 1,
            "detail": "legacy recovery",
        }
    )
    assert ok is True
    assert "Recovery" in detail
