from artmach_assistant.core.assistant import AssistantEngine


def test_revision_mismatch_persists_stale_cycle(monkeypatch, tmp_path):
    engine = AssistantEngine.__new__(AssistantEngine)
    monkeypatch.setattr(engine, "own_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        engine,
        "_current_own_code_revision",
        lambda root=None: "new-revision",
    )

    saved = {}

    def save(stage, detail, **kwargs):
        saved["stage"] = stage
        saved["detail"] = detail
        saved["kwargs"] = kwargs

    monkeypatch.setattr(engine, "_save_own_code_cycle", save)

    ok, detail = engine._verify_interrupted_engineering_recovery(
        {
            "source_revision": "old-revision",
            "failures": ["x"],
            "attempt": 0,
            "changed_paths": ["core/assistant.py"],
            "version_summary": "v",
        }
    )

    assert ok is False
    assert saved["stage"] == "stale"
    assert saved["kwargs"]["source_revision"] == "old-revision"
    assert "stale recovery evidence" in detail.lower()


def test_cycle_report_reflects_persisted_stale_after_failed_recovery(monkeypatch):
    engine = AssistantEngine.__new__(AssistantEngine)

    initial = {
        "version": 4,
        "stage": "recovery_required",
        "detail": "old recovery",
        "failures": [],
        "attempt": 0,
        "changed_paths": ["core/assistant.py"],
        "validation_summary": "",
        "version_summary": "",
        "owner_pid": 1,
    }
    stale = dict(initial)
    stale.update(
        {
            "stage": "stale",
            "detail": "stale invalidated",
            "validation_summary": "revision mismatch",
        }
    )

    loads = iter((initial, stale))
    monkeypatch.setattr(engine, "_load_own_code_cycle", lambda: next(loads))
    monkeypatch.setattr(
        engine,
        "_verify_interrupted_engineering_recovery",
        lambda cycle: (False, "revision mismatch"),
    )

    import artmach_assistant.core.assistant as assistant_module
    monkeypatch.setattr(assistant_module.os, "getpid", lambda: 999)

    result = engine.own_code_cycle_report()

    assert "eski taslak kaydi gecersizlestirildi" in engine.command_key(result)
    assert "revision mismatch" in result
