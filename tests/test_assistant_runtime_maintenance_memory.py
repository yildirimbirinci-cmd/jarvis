from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.maintenance_advisor import MaintenanceAdvisor
from artmach_assistant.core.notification_store import NotificationStore
from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_improvement_service import (
    ImprovementEvidence,
    ImprovementFinding,
    ProjectImprovementAssessment,
    ProjectProfile,
)
from artmach_assistant.core.runtime_observability import (
    RuntimeEventStore,
    RuntimeHealthAnalyzer,
)


def _architecture_assessment(root: Path) -> ProjectImprovementAssessment:
    finding = ImprovementFinding(
        finding_id="ARC-123456789A",
        severity="high",
        category="dependency_cycle",
        title="Dependency cycle",
        explanation="a.py and b.py import each other.",
        confidence=0.92,
        evidence=(
            ImprovementEvidence(
                source="dependency_graph",
                path="a.py",
                line=1,
                detail="a.py -> b.py",
                metric="cycle_edge",
            ),
        ),
        affected_paths=("a.py", "b.py"),
        recommendation="Make the dependency direction one-way.",
        acceptance_criteria=("The cycle must disappear.",),
        research_query="Python dependency cycle official guidance",
    )
    return ProjectImprovementAssessment(
        root=str(root),
        generated_at="2026-07-31T00:00:00+00:00",
        profile=ProjectProfile(
            languages=(("Python", 2),),
            frameworks=("pytest",),
            manifests=("pyproject.toml",),
            source_files=2,
            test_files=1,
        ),
        findings=(finding,),
        scanned_files=3,
    )


def _runtime_report(root: Path, *, scope: str = "own_code"):
    store = RuntimeEventStore(root / "events.json")
    for _index in range(3):
        store.record(
            component="Worker",
            action="execute",
            status="failed",
            workspace=root,
            scope=scope,
            source_path="core/worker.py" if scope == "own_code" else "src/worker.py",
            symbol="Worker.execute",
            message="repeatable failure",
            error_type="RuntimeError",
        )
    return store, RuntimeHealthAnalyzer(store).analyze(workspace=root)


def test_project_memory_commands_persist_goal_task_and_completion(tmp_path: Path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.workspace = SimpleNamespace(require_root=lambda: tmp_path)
    engine.own_project_root = lambda: tmp_path / "jarvis"
    engine.project_memory = ProjectDevelopmentMemory(tmp_path / "memory")

    goal = engine._project_memory_request(
        "proje hedefini kaydet kullanıcı onaylı güvenli bir masaüstü aracı geliştir"
    )
    task = engine._project_memory_request("görev ekle ayarlar ekranını tamamla")
    report = engine._project_memory_request("proje hafızasını göster")

    assert "ana hedefini kaydettim" in goal
    assert "Görev kaydedildi" in task
    task_id = task.split("[", 1)[1].split("]", 1)[0]
    assert "güvenli bir masaüstü aracı" in report
    assert task_id in report

    completed = engine._project_memory_request(f"{task_id} görevini tamamla")
    updated = engine._project_memory_request("proje hafızasını göster")

    assert "tamamlandı" in completed
    assert f"[{task_id}] ayarlar ekranını tamamla (completed)" in updated


def test_maintenance_review_combines_runtime_and_static_evidence(tmp_path: Path) -> None:
    store, _report = _runtime_report(tmp_path)
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.workspace = SimpleNamespace(require_root=lambda: tmp_path)
    engine.own_project_root = lambda: tmp_path
    engine.runtime_events = store
    engine.runtime_health = RuntimeHealthAnalyzer(store)
    engine.notifications = NotificationStore(tmp_path / "notifications.json")
    engine.maintenance_advisor = MaintenanceAdvisor(
        tmp_path / "maintenance.json", engine.notifications
    )
    engine.project_improvements = SimpleNamespace(
        assessment=lambda *, own_code, refresh: _architecture_assessment(tmp_path)
    )
    engine.last_action_context = None

    answer = engine.maintenance_review(own_code=True, refresh_architecture=True)

    assert "JARVIS BAKIM" in answer
    assert "RUN-" in answer
    assert "ARC-123456789A" in answer
    assert len(engine.notifications.load()) == 2


def test_runtime_finding_for_own_code_creates_scoped_repair_session(tmp_path: Path) -> None:
    _store, report = _runtime_report(tmp_path, scope="own_code")
    finding = report.findings[0]
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._last_runtime_health_report = report
    engine.own_project_root = lambda: tmp_path
    engine._development_root = lambda *, own_code: tmp_path
    engine.self_repair_sessions = __import__(
        "artmach_assistant.core.self_repair_session", fromlist=["SelfRepairSessionStore"]
    ).SelfRepairSessionStore(tmp_path / "self_repair.json")
    engine._current_source_fingerprint = lambda: "SOURCE-1"

    answer = engine.prepare_runtime_improvement_implementation(finding.finding_id)
    session = engine.self_repair_sessions.load()

    assert session is not None
    assert session.finding_id == finding.finding_id
    assert session.plan_id in answer
    assert session.approved_paths == ("core/worker.py",)
    assert session.approved_symbols == ("Worker.execute",)
    assert "Henüz patch üretilmedi" in answer


def test_runtime_finding_for_selected_project_prepares_but_does_not_apply(
    tmp_path: Path,
) -> None:
    _store, report = _runtime_report(tmp_path, scope="selected_project")
    finding = report.findings[0]
    proposal = EditProposal(
        "runtime fix",
        [ProposedFileChange("src/worker.py", "reason", "old", "new", True)],
    )
    captured: dict[str, object] = {}

    class _Runtime:
        def prepare_edit(self, instruction: str, **kwargs):
            captured["instruction"] = instruction
            captured.update(kwargs)
            return proposal

    engine = AssistantEngine.__new__(AssistantEngine)
    engine._last_runtime_health_report = report
    engine.own_project_root = lambda: tmp_path / "jarvis"
    engine.workspace = SimpleNamespace(require_root=lambda: tmp_path)
    engine.project_improvements = _Runtime()

    answer = engine.prepare_runtime_improvement_implementation(finding.finding_id)

    assert "Henüz hiçbir dosya değişmedi" in answer
    assert captured["approved_paths"] == ("src/worker.py",)
    assert finding.finding_id in str(captured["evidence_context"])


def test_handle_records_failure_with_source_and_symbol(tmp_path: Path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.runtime_events = RuntimeEventStore(tmp_path / "events.json")
    engine.own_project_root = lambda: tmp_path
    engine.self_awareness = SimpleNamespace(mark_user_activity=lambda: None)
    engine.conversation_runtime = None
    engine.handle_local_command = lambda _text: (_ for _ in ()).throw(
        ValueError("synthetic failure")
    )

    with pytest.raises(ValueError, match="synthetic failure"):
        engine.handle("test command")

    events = engine.runtime_events.load()
    assert len(events) == 1
    assert events[0].status == "failed"
    assert events[0].source_path == "core/assistant.py"
    assert events[0].symbol == "AssistantEngine.handle"
    assert events[0].metadata == {
        "input_chars": 12,
        "aggregate_operation": True,
        "health_excluded": True,
    }


def test_handle_appends_new_maintenance_warning_after_command(
    tmp_path: Path,
) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.runtime_events = RuntimeEventStore(tmp_path / "events.json")
    engine.own_project_root = lambda: tmp_path
    engine.self_awareness = SimpleNamespace(mark_user_activity=lambda: None)
    engine.conversation_runtime = None
    engine.handle_local_command = lambda _text: "Normal cevap"
    remembered: list[tuple[str, str]] = []
    engine.dialogue = SimpleNamespace(
        remember=lambda raw, answer: remembered.append((raw, answer))
    )
    engine.command_router = SimpleNamespace(behavior=object())
    engine.proactive_advisor = SimpleNamespace(suggestion=lambda *_args: "")
    engine._automatic_maintenance_note = lambda: (
        "Bak\u0131m uyar\u0131s\u0131 [RUN-123456789A]"
    )

    answer = engine.handle("merhaba")

    assert answer == "Normal cevap"
    assert engine.take_pending_maintenance_notice() == (
        "Bak\u0131m uyar\u0131s\u0131 [RUN-123456789A]"
    )
    assert engine.take_pending_maintenance_notice() == ""
    assert remembered == [("merhaba", "Normal cevap")]

def test_spoken_response_omits_automatic_maintenance_warning() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)

    spoken = engine.spoken_response(
        "Özelliklerimi anlattım.\n\n"
        "Bakım uyarısı [RUN-CB636AB534]: Tekrarlanan hata: VoiceService.speech_turn. "
        "Kanıt: 5 tekrar."
    )

    assert spoken == "Özelliklerimi anlattım."
    assert "Bakım uyarısı" not in spoken
