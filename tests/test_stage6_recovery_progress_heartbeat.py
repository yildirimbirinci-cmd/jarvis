from __future__ import annotations
from artmach_assistant.core.assistant import AssistantEngine

def _engine():
    engine = object.__new__(AssistantEngine)
    writes = []
    engine._save_own_validation = lambda *args, **kwargs: None
    engine._save_own_code_cycle = lambda stage, detail, **kwargs: writes.append((stage, detail, dict(kwargs)))
    engine._compile_own_code = lambda: (True, "compile ok")
    engine._runtime_health_check = lambda: (True, "runtime ok")
    engine._run_own_tests = lambda: (True, "tests ok")
    engine._test_failure_ids = lambda output: set()
    return engine, writes

def test_recovery_validation_persists_progress_before_long_gates():
    engine, writes = _engine()
    ok, detail = engine._validate_recovered_engineering_source({
        "stage": "recovery_required", "detail": "interrupted apply", "attempt": 1,
        "changed_paths": ["core/assistant.py"], "failures": [],
    })
    assert ok is True
    summaries = [row[2].get("validation_summary", "") for row in writes]
    assert len(summaries) >= 3
    assert "compile check is running" in summaries[0]
    assert "clean runtime check is running" in summaries[1]
    assert "regression tests are running" in summaries[2]

def test_recovery_progress_never_marks_recovered_early():
    engine, writes = _engine()
    engine._validate_recovered_engineering_source({
        "stage": "recovery_required", "detail": "interrupted apply", "attempt": 1,
        "changed_paths": ["core/assistant.py"], "failures": [],
    })
    assert writes
    assert all(stage == "recovery_required" for stage, _detail, _kwargs in writes)
