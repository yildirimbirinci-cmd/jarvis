from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.runtime_observability import (
    RuntimeEventStore,
    RuntimeFinding,
    RuntimeHealthAnalyzer,
    RuntimeHealthReport,
)
from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


def _finding(tmp_path: Path, error_type: str = "TypeError") -> RuntimeFinding:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "core").mkdir(exist_ok=True)
    (project / "core" / "assistant.py").write_text(
        "class AssistantEngine:\n    def handle(self):\n        return None\n",
        encoding="utf-8",
    )
    return RuntimeFinding(
        finding_id="RUN-A1B2C3D4E5",
        severity="medium",
        category="repeated_runtime_failure",
        title="Tekrarlanan hata: TaskOrchestrator.execute_task",
        explanation=f"Ayni hata imzasi 3 kez olustu. Son hata turu: {error_type}.",
        confidence=0.88,
        occurrence_count=3,
        last_seen="2026-08-10T07:00:00+00:00",
        workspace=str(project),
        scope="own_code",
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
        evidence=(),
        recommendation="En kucuk duzeltmeyi hazirla.",
        acceptance_criteria=("Hata tekrar olusmamali.",),
        research_query="runtime diagnostics",
        error_type=error_type,
    )


def _engine(tmp_path: Path, finding: RuntimeFinding) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.self_repair_sessions = SelfRepairSessionStore(
        tmp_path / "self_repair_session.json"
    )
    project = Path(finding.workspace)
    engine._development_root = MethodType(lambda self, *, own_code: project, engine)
    engine._current_source_fingerprint = MethodType(
        lambda self: "stage4-auto-entry-fingerprint",
        engine,
    )
    engine._load_own_code_plan = MethodType(lambda self: None, engine)

    def _prepare(self, finding_id: str) -> str:
        assert finding_id == finding.finding_id
        self._self_repair_store().create(
            finding_id=finding.finding_id,
            instruction="controlled automatic engineering entry",
            approved_paths=finding.affected_paths,
            approved_symbols=finding.affected_symbols,
            evidence="controlled runtime evidence",
            acceptance=finding.acceptance_criteria,
            source_fingerprint=self._current_source_fingerprint(),
        )
        return "planned"

    engine.prepare_runtime_improvement_implementation = MethodType(_prepare, engine)
    return engine


def test_analyzer_preserves_repairable_error_type_and_real_target(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = RuntimeEventStore(tmp_path / "runtime_events.json")
    metadata = {
        "action_path": "core/assistant.py",
        "action_symbol": "AssistantEngine.handle",
    }
    for _ in range(3):
        store.record(
            component="TaskOrchestrator",
            action="execute_task",
            status="failed",
            workspace=project,
            scope="own_code",
            source_path="core/task_orchestrator.py",
            symbol="TaskOrchestrator.wrap.execute",
            error_type="TypeError",
            message="controlled type error",
            metadata=metadata,
        )
    finding = next(
        item
        for item in RuntimeHealthAnalyzer(store).analyze(workspace=project).findings
        if item.category == "repeated_runtime_failure"
    )
    assert finding.error_type == "TypeError"
    assert finding.affected_paths == ("core/assistant.py",)
    assert finding.affected_symbols == ("AssistantEngine.handle",)


def test_repairable_failure_creates_only_planned_engineering_state(tmp_path: Path) -> None:
    finding = _finding(tmp_path)
    engine = _engine(tmp_path, finding)
    session = engine._prepare_automatic_runtime_failure_entry(finding)
    assert session is not None
    assert session.state == "planned"
    assert session.finding_id == finding.finding_id
    assert session.approved_paths == ("core/assistant.py",)
    assert session.approved_symbols == ("AssistantEngine.handle",)
    assert session.attempts == 0


def test_generic_runtime_error_does_not_create_automatic_state(tmp_path: Path) -> None:
    finding = _finding(tmp_path, "RuntimeError")
    engine = _engine(tmp_path, finding)
    assert engine._prepare_automatic_runtime_failure_entry(finding) is None
    assert engine.self_repair_sessions.load() is None


def test_existing_active_repair_session_is_not_overwritten(tmp_path: Path) -> None:
    finding = _finding(tmp_path)
    engine = _engine(tmp_path, finding)
    existing = engine.self_repair_sessions.create(
        finding_id="RUN-1111111111",
        instruction="existing repair",
        approved_paths=("core/existing.py",),
        approved_symbols=("Existing.run",),
        evidence="existing evidence",
        acceptance=("keep existing",),
        source_fingerprint="stage4-auto-entry-fingerprint",
    )
    assert engine._prepare_automatic_runtime_failure_entry(finding) is None
    current = engine.self_repair_sessions.load()
    assert current is not None
    assert current.plan_id == existing.plan_id
    assert current.finding_id == existing.finding_id


def test_existing_own_code_plan_blocks_automatic_entry(tmp_path: Path) -> None:
    finding = _finding(tmp_path)
    engine = _engine(tmp_path, finding)
    engine._load_own_code_plan = MethodType(
        lambda self: {"status": "approved", "plan_id": "PLAN-EXISTING"},
        engine,
    )
    assert engine._prepare_automatic_runtime_failure_entry(finding) is None
    assert engine.self_repair_sessions.load() is None


def test_maintenance_note_prepares_state_without_patch_or_apply(tmp_path: Path) -> None:
    finding = _finding(tmp_path)
    engine = _engine(tmp_path, finding)
    report = RuntimeHealthReport(
        generated_at="2026-08-10T07:00:00+00:00",
        workspace=finding.workspace,
        lookback_hours=168,
        event_count=3,
        completed_count=0,
        failed_count=3,
        cancelled_count=0,
        warning_count=0,
        findings=(finding,),
    )
    alert = SimpleNamespace(
        finding_id=finding.finding_id,
        title=finding.title,
        evidence_summary="3 tekrar",
    )
    engine.project_improvements = None
    engine._runtime_health_service = MethodType(
        lambda self: SimpleNamespace(analyze=lambda **kwargs: report),
        engine,
    )
    engine._maintenance_service = MethodType(
        lambda self: SimpleNamespace(
            evaluate=lambda *args, **kwargs: SimpleNamespace(new_alerts=(alert,))
        ),
        engine,
    )
    output = engine._automatic_maintenance_note()
    session = engine.self_repair_sessions.load()
    assert session is not None
    assert session.state == "planned"
    assert session.plan_id in output
    assert "Patch üretilmedi" in output
    assert "apply başlatılmadı" in output
