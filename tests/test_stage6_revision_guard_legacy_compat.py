from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine


def test_legacy_recovery_without_source_revision_does_not_query_revision(monkeypatch) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)

    def forbidden_revision(root=None):
        raise AssertionError("legacy cycle must not query source revision")

    monkeypatch.setattr(engine, "_current_own_code_revision", forbidden_revision)
    monkeypatch.setattr(engine, "_compile_own_code", lambda: (True, "compile ok"))
    monkeypatch.setattr(engine, "_runtime_health_check", lambda: (True, "runtime ok"))
    monkeypatch.setattr(engine, "_run_own_tests", lambda: (True, "1 passed"))
    monkeypatch.setattr(engine, "_save_own_validation", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_save_own_code_cycle", lambda *args, **kwargs: None)

    ok, _detail = engine._validate_recovered_engineering_source(
        {"failures": [], "changed_paths": [], "attempt": 0}
    )

    assert ok is True


def test_revision_guard_still_queries_revision_when_checkpoint_has_one(monkeypatch) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    calls = []

    def current_revision(root=None):
        calls.append(root)
        return "rev-a"

    monkeypatch.setattr(engine, "_current_own_code_revision", current_revision)
    monkeypatch.setattr(engine, "_compile_own_code", lambda: (True, "compile ok"))
    monkeypatch.setattr(engine, "_runtime_health_check", lambda: (True, "runtime ok"))
    monkeypatch.setattr(engine, "_run_own_tests", lambda: (True, "1 passed"))
    monkeypatch.setattr(engine, "_save_own_validation", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_save_own_code_cycle", lambda *args, **kwargs: None)

    ok, _detail = engine._validate_recovered_engineering_source(
        {
            "source_revision": "rev-a",
            "failures": [],
            "changed_paths": [],
            "attempt": 0,
        }
    )

    assert ok is True
    assert len(calls) == 2
