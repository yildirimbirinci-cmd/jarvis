from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine(tmp_path):
    engine = object.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine.command_key = lambda text: " ".join(str(text).casefold().split())
    target = SimpleNamespace(
        title="Tekrarlanan yavas islem: TaskOrchestrator.execute_task",
        explanation="Islem 17 kez kendi 30000 ms esigini asti.",
        occurrence_count=17,
        confidence=0.96,
        affected_paths=("core/task_orchestrator.py",),
        affected_symbols=("TaskOrchestrator.wrap.execute",),
        evidence=(SimpleNamespace(
            source_path="core/task_orchestrator.py",
            symbol="TaskOrchestrator.wrap.execute",
            detail="TaskOrchestrator.execute_task completed",
        ),),
    )
    unrelated = SimpleNamespace(
        title="Tekrarlanan yavas islem: AssistantEngine.own_code_tests",
        explanation="Baska bulgu",
        occurrence_count=4,
        confidence=0.88,
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine._run_own_tests",),
        evidence=(),
    )
    engine._runtime_health_service = lambda: SimpleNamespace(
        analyze=lambda workspace: SimpleNamespace(findings=(target, unrelated))
    )
    return engine


def test_targeted_runtime_report_filters_unrelated_findings(tmp_path):
    result = _engine(tmp_path)._targeted_runtime_finding_report_request(
        "TaskOrchestrator.execute_task ile ilgili aktif runtime bulgusunu "
        "yalniz yerel kanitlarla arastir. Hicbir plan, patch veya kod "
        "degisikligi baslatma. Bana yalniz bulgunun nedenini, konumunu "
        "ve mevcut kaniti ozetle."
    )
    assert result is not None
    assert result.startswith("Neden:\n")
    assert "\n\nKonum:\n" in result
    assert "\n\nKanit:\n" in result
    assert "TaskOrchestrator.execute_task" in result
    assert "AssistantEngine.own_code_tests" not in result


def test_targeted_runtime_report_uses_only_three_sections(tmp_path):
    result = _engine(tmp_path)._targeted_runtime_finding_report_request(
        "Yalniz TaskOrchestrator.execute_task bulgusunu raporla. "
        "Baska bulgu, cozum onerisi, plan veya patch ekleme. "
        "Sadece su uc baslik olsun: Neden, Konum, Kanit."
    )
    assert result is not None
    assert result.count("Neden:") == 1
    assert result.count("Konum:") == 1
    assert result.count("Kanit:") == 1
    assert "AssistantEngine.own_code_tests" not in result


def test_write_intent_is_not_stolen_by_scoped_report(tmp_path):
    result = _engine(tmp_path)._targeted_runtime_finding_report_request(
        "TaskOrchestrator.execute_task bulgusunu duzelt ve patch hazirla."
    )
    assert result is None


def test_handle_routes_scoped_runtime_report_before_generic_repair(monkeypatch):
    engine = object.__new__(AssistantEngine)
    monkeypatch.setattr(engine, "normalize_address", lambda text: text)
    monkeypatch.setattr(engine, "command_key", lambda text: text.casefold())
    monkeypatch.setattr(engine, "_asks_for_engineering_state_only", lambda text: False)
    monkeypatch.setattr(engine, "_patch_session_command_request", lambda text: None)
    monkeypatch.setattr(engine, "_retest_command_request", lambda text: None)
    monkeypatch.setattr(engine, "_targeted_runtime_finding_report_request", lambda text: "SCOPED-RUNTIME-REPORT")
    monkeypatch.setattr(engine, "_reserved_self_repair_request", lambda text: (_ for _ in ()).throw(AssertionError("generic self-repair must not run first")))
    result = engine.handle_local_command("TaskOrchestrator.execute_task runtime bulgusunu raporla.")
    assert result == "SCOPED-RUNTIME-REPORT"


def test_handle_routes_broad_runtime_visibility_before_generic_repair(monkeypatch):
    engine = object.__new__(AssistantEngine)
    monkeypatch.setattr(engine, "normalize_address", lambda text: text)
    monkeypatch.setattr(engine, "command_key", lambda text: text.casefold())
    monkeypatch.setattr(engine, "_asks_for_engineering_state_only", lambda text: False)
    monkeypatch.setattr(engine, "_patch_session_command_request", lambda text: None)
    monkeypatch.setattr(engine, "_retest_command_request", lambda text: None)
    monkeypatch.setattr(engine, "_runtime_visibility_request", lambda text: "RUNTIME-VISIBILITY")
    monkeypatch.setattr(
        engine,
        "_reserved_self_repair_request",
        lambda text: (_ for _ in ()).throw(
            AssertionError("generic self-repair must not run first")
        ),
    )

    result = engine.handle_local_command("Aktif runtime bulgularini goster.")

    assert result == "RUNTIME-VISIBILITY"


def test_health_report_routes_to_deterministic_read_only_review(monkeypatch):
    engine = object.__new__(AssistantEngine)
    monkeypatch.setattr(engine, "command_key", lambda text: text.casefold())
    monkeypatch.setattr(engine, "own_code_review_report", lambda: "HEALTH-REPORT")

    result = engine._runtime_visibility_request(
        "Kanita dayali sistem saglik raporunu goster. Hicbir kodu degistirme."
    )

    assert result == "HEALTH-REPORT"
