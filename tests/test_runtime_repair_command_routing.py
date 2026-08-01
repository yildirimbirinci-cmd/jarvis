from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.self_repair_session import SelfRepairSessionStore
from artmach_assistant.core.runtime_observability import (
    RuntimeEventStore,
    RuntimeHealthAnalyzer,
    RuntimeFinding,
    RuntimeHealthReport,
)


def test_extract_runtime_finding_id_accepts_typed_and_spoken_separator() -> None:
    assert AssistantEngine._extract_runtime_finding_id(
        "RUN-88D9CA6351 bulgusunu düzelt"
    ) == "RUN-88D9CA6351"
    assert AssistantEngine._extract_runtime_finding_id(
        "run 88d9ca6351 bulgusunu onar"
    ) == "RUN-88D9CA6351"
    assert AssistantEngine._extract_runtime_finding_id("RUN-88D9CA635 bulgusunu düzelt") is None


def test_maintenance_request_routes_exact_runtime_id_without_static_review() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    captured: list[str] = []
    engine.prepare_runtime_improvement_implementation = lambda finding_id: captured.append(finding_id) or "TARGETED"

    answer = engine._maintenance_request("RUN 88D9CA6351 bulgusunu düzelt")

    assert answer == "TARGETED"
    assert captured == ["RUN-88D9CA6351"]


def test_runtime_command_is_not_consumed_as_old_plan_clarification(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(assistant_module, "OWN_CODE_PLAN_FILE", tmp_path / "plan.json")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._save_own_code_plan({
        "version": 2,
        "status": "needs_clarification",
        "instruction": "kaynak kodlarını düzelt",
        "question": "Hangi davranış değişmeli?",
        "candidate_files": [],
        "acceptance": [],
    })

    assert engine._handle_own_code_plan_follow_up(
        "RUN-88D9CA6351 bulgusunu düzelt"
    ) is None
    assert engine._load_own_code_plan()["status"] == "needs_clarification"


def test_plan_approval_phrase_keeps_runtime_scope(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(assistant_module, "OWN_CODE_PLAN_FILE", tmp_path / "plan.json")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=object())
    engine._save_own_code_plan({
        "version": 2,
        "status": "awaiting_approval",
        "plan_kind": "runtime_repair",
        "plan_id": "RPR-88D9CA6351",
        "finding_id": "RUN-88D9CA6351",
        "instruction": "Yalnızca kanıtlı ses çıkışı iptalini düzelt.",
        "candidate_files": ["core/voice_service.py"],
        "approved_paths": ["core/voice_service.py"],
        "approved_symbols": ["VoiceService._play_audio_resilient"],
        "acceptance": ["Yeni regresyon oluşmamalı."],
    })
    captured: dict[str, object] = {}

    def prepare(instruction: str, **kwargs):
        captured["instruction"] = instruction
        captured.update(kwargs)
        return "TARGETED_PATCH"

    engine.prepare_own_code_proposal = prepare

    answer = engine._handle_own_code_plan_follow_up("planı onayla")

    assert answer == "TARGETED_PATCH"
    assert captured["production_repair"] is True
    assert captured["approved_paths"] == ("core/voice_service.py",)
    assert captured["approved_symbols"] == ("VoiceService._play_audio_resilient",)
    assert captured["plan_id"] == "RPR-88D9CA6351"
    assert engine._load_own_code_plan()["status"] == "approved"


def test_handle_local_command_checks_runtime_before_stale_plan() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.agent_tool_commands = SimpleNamespace(
        handle=lambda _text: SimpleNamespace(handled=False, response="")
    )
    engine.filesystem_tool_conversation = SimpleNamespace(
        handle=lambda _text: SimpleNamespace(handled=False, response="")
    )
    engine._handle_action_follow_up = lambda _text: None
    engine._reserved_self_repair_request = lambda _text: "RUNTIME_ROUTE"
    engine._maintenance_request = lambda _text: "LATE_RUNTIME_ROUTE"
    engine._handle_own_code_plan_follow_up = lambda _text: "STALE_PLAN"

    assert engine.handle_local_command(
        "RUN-88D9CA6351 bulgusunu düzelt"
    ) == "RUNTIME_ROUTE"


def test_review_follow_up_hides_test_fixture_security_issue() -> None:
    report = "\n".join((
        "KOD INCELEME",
        "SECURITY: 1 | COMPLEXITY: 1",
        "[SECURITY] tests/test_fixture.py:36 — Dinamik kod çalıştırma kullanımı",
        "[COMPLEXITY] core/assistant.py:10 — handle: 80 satırdan uzun fonksiyon",
    ))

    answer = AssistantEngine._review_follow_up_report(report)

    assert "tests/test_fixture.py" not in answer
    assert "core/assistant.py" in answer
    assert "SECURITY: 1" not in answer


def test_expected_voice_cancellations_and_intent_fallback_do_not_create_findings(
    tmp_path: Path,
) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(5):
        store.record(
            component="VoiceService",
            action="speech_turn",
            status="cancelled",
            workspace=tmp_path,
            scope="voice",
            source_path="core/voice_service.py",
            symbol="VoiceService.listen_utterance",
            message="Yeni konuşma turu başladı; eski konuşma turu iptal edildi.",
            error_type="InterruptedError",
        )
    for _ in range(6):
        store.record(
            component="LocalDialogueManager",
            action="intent_model",
            status="warning",
            workspace=tmp_path,
            scope="model",
            source_path="core/local_dialogue.py",
            symbol="LocalDialogueManager.interpret",
            message="Yerel sohbet modeli kullanılabilir bir yanıt üretmedi.",
        )

    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)

    assert report.findings == ()


def test_unexpected_task_cancellation_remains_actionable(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(3):
        store.record(
            component="TaskOrchestrator",
            action="execute_task",
            status="cancelled",
            workspace=tmp_path,
            scope="task",
            source_path="core/task_orchestrator.py",
            symbol="TaskOrchestrator.execute",
            message="Görev zaman aşımı nedeniyle iptal edildi.",
            error_type="TimeoutError",
        )

    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)

    assert len(report.findings) == 1
    assert report.findings[0].category == "repeated_cancellation"


def test_fresh_runtime_report_does_not_fall_back_to_stale_cached_finding(tmp_path: Path) -> None:
    stale_finding = RuntimeFinding(
        finding_id="RUN-88D9CA6351",
        severity="medium",
        category="repeated_cancellation",
        title="Tekrarlanan iptal: VoiceService.audio_output_playback",
        explanation="Expected barge-in cancellation.",
        confidence=0.9,
        occurrence_count=4,
        last_seen="2026-08-01T00:00:00+00:00",
        workspace=str(tmp_path),
        scope="voice",
        affected_paths=("core/voice_service.py",),
        affected_symbols=("VoiceService._play_audio_resilient",),
        evidence=(),
        recommendation="Do not repair expected control flow.",
        acceptance_criteria=("No false maintenance alert.",),
        research_query="",
    )
    stale_report = RuntimeHealthReport(
        generated_at="2026-08-01T00:00:00+00:00",
        workspace=str(tmp_path),
        lookback_hours=168,
        event_count=4,
        completed_count=0,
        failed_count=0,
        cancelled_count=4,
        warning_count=0,
        findings=(stale_finding,),
    )

    fresh_store = RuntimeEventStore(tmp_path / "fresh.json")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._last_runtime_health_report = stale_report
    engine._runtime_health_service = lambda: RuntimeHealthAnalyzer(fresh_store)
    engine._development_root = lambda *, own_code: tmp_path

    assert engine._find_runtime_finding(stale_finding.finding_id) is None



def test_runtime_finding_to_scoped_proposal_flow_survives_stale_generic_plan(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(assistant_module, "OWN_CODE_PLAN_FILE", tmp_path / "plan.json")
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(3):
        store.record(
            component="Worker",
            action="execute",
            status="failed",
            workspace=tmp_path,
            scope="own_code",
            source_path="core/worker.py",
            symbol="Worker.execute",
            message="repeatable failure",
            error_type="RuntimeError",
        )
    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)
    finding = report.findings[0]

    engine = AssistantEngine.__new__(AssistantEngine)
    engine._last_runtime_health_report = report
    engine.runtime_health = RuntimeHealthAnalyzer(store)
    engine.runtime_events = store
    engine.own_project_root = lambda: tmp_path
    engine._development_root = lambda *, own_code: tmp_path
    engine._save_own_code_plan({
        "version": 2,
        "status": "needs_clarification",
        "instruction": "kaynak kodlarını düzelt",
        "question": "Hangi davranış değişmeli?",
        "candidate_files": [],
        "acceptance": [],
    })

    engine.self_repair_sessions = SelfRepairSessionStore(tmp_path / "self_repair.json")
    engine._current_source_fingerprint = lambda: "SOURCE-1"
    planned = engine._maintenance_request(f"{finding.finding_id} bulgusunu düzelt")
    saved = engine.self_repair_sessions.load()

    assert "RPR-" in planned
    assert saved is not None
    assert saved.approved_paths == ("core/worker.py",)

    captured: dict[str, object] = {}
    engine.editor = SimpleNamespace(pending=None)

    def prepare(instruction: str, **kwargs):
        captured["instruction"] = instruction
        captured.update(kwargs)
        engine.editor.pending = SimpleNamespace(files=[])
        return "SCOPED_PROPOSAL"

    engine.prepare_own_code_proposal = prepare
    engine._self_repair_store = lambda: engine.self_repair_sessions
    # Avoid proposal fingerprint internals in this routing-only test.
    engine._prepare_active_self_repair_proposal = lambda session: (
        captured.update({
            "approved_paths": session.approved_paths,
            "approved_symbols": session.approved_symbols,
        })
        or "SCOPED_PROPOSAL"
    )
    assert engine._reserved_self_repair_request("başla") == "SCOPED_PROPOSAL"
    assert captured["approved_paths"] == ("core/worker.py",)
    assert captured["approved_symbols"] == ("Worker.execute",)


def test_bare_start_does_not_reach_general_model_without_pending_state() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.agent_tool_commands = SimpleNamespace(
        handle=lambda _text: SimpleNamespace(handled=False, response="")
    )
    engine.filesystem_tool_conversation = SimpleNamespace(
        handle=lambda _text: SimpleNamespace(handled=False, response="")
    )
    engine.dialogue = SimpleNamespace(remember=lambda *_args: None)
    route_names = (
        "_handle_action_follow_up", "_maintenance_request",
        "_handle_own_code_plan_follow_up", "_conversation_context_request",
        "_project_bootstrap_request", "_project_progress_request",
        "_project_development_request", "_project_memory_request",
        "_project_improvement_request", "_internet_research_request",
        "_model_lab_request", "_own_code_authority_request",
        "_own_code_cycle_request", "_own_code_repair_request",
        "_own_code_version_request", "_own_code_history_request",
        "_own_code_activity_request", "_own_code_acceptance_request",
        "_own_code_test_request", "_own_code_plan_request",
        "_own_code_risk_request", "_project_edit_approval_request",
        "_own_code_approval_request", "_fast_capability_question",
        "_own_code_change_request", "_own_code_request",
    )
    for name in route_names:
        setattr(engine, name, lambda _text, _name=name: None)
    engine._local_model_request = lambda _text: "MODEL_SHOULD_NOT_RUN"

    answer = engine.handle_local_command("başla")

    assert "Başlatılacak onay bekleyen" in answer
    assert "MODEL_SHOULD_NOT_RUN" not in answer
