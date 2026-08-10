from __future__ import annotations

from pathlib import Path
from types import MethodType

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.runtime_observability import RuntimeFinding
from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


def _finding(tmp_path: Path) -> RuntimeFinding:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "core").mkdir(exist_ok=True)
    (project / "core" / "assistant.py").write_text(
        "class AssistantEngine:\n"
        "    def handle(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    return RuntimeFinding(
        finding_id="RUN-B1C2D3E4F5",
        severity="medium",
        category="repeated_runtime_failure",
        title="Tekrarlanan hata: TaskOrchestrator.execute_task",
        explanation="Ayni hata imzasi 3 kez olustu. Son hata turu: TypeError.",
        confidence=0.9,
        occurrence_count=3,
        last_seen="2026-08-10T08:30:00+00:00",
        workspace=str(project),
        scope="own_code",
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
        evidence=(),
        recommendation="En kucuk guvenli duzeltmeyi hazirla.",
        acceptance_criteria=("TypeError tekrar olusmamali.",),
        research_query="TypeError runtime diagnostics",
        error_type="TypeError",
    )


def _engine(
    tmp_path: Path,
    finding: RuntimeFinding,
    *,
    fingerprint: str = "stage4-restart-source",
) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.self_repair_sessions = SelfRepairSessionStore(
        tmp_path / "self_repair_session.json"
    )
    project = Path(finding.workspace)
    engine._development_root = MethodType(
        lambda self, *, own_code: project,
        engine,
    )
    engine._current_source_fingerprint = MethodType(
        lambda self: fingerprint,
        engine,
    )
    engine._load_own_code_plan = MethodType(lambda self: None, engine)

    def _prepare(self, finding_id: str) -> str:
        assert finding_id == finding.finding_id
        self._self_repair_store().create(
            finding_id=finding.finding_id,
            instruction="controlled restart-persistent runtime repair",
            approved_paths=finding.affected_paths,
            approved_symbols=finding.affected_symbols,
            evidence="controlled runtime evidence",
            acceptance=finding.acceptance_criteria,
            source_fingerprint=self._current_source_fingerprint(),
        )
        return "planned"

    engine.prepare_runtime_improvement_implementation = MethodType(_prepare, engine)
    return engine


def test_automatic_planned_runtime_repair_survives_engine_restart(
    tmp_path: Path,
) -> None:
    finding = _finding(tmp_path)

    before_restart = _engine(tmp_path, finding)
    created = before_restart._prepare_automatic_runtime_failure_entry(finding)

    assert created is not None
    assert created.state == "planned"
    assert created.finding_id == finding.finding_id
    assert created.approved_paths == ("core/assistant.py",)
    assert created.approved_symbols == ("AssistantEngine.handle",)

    after_restart = _engine(tmp_path, finding)
    restored = after_restart._active_self_repair_session()

    assert restored is not None
    assert restored.plan_id == created.plan_id
    assert restored.session_id == created.session_id
    assert restored.state == "planned"
    assert restored.finding_id == finding.finding_id
    assert restored.approved_paths == ("core/assistant.py",)
    assert restored.approved_symbols == ("AssistantEngine.handle",)
    assert restored.source_fingerprint == "stage4-restart-source"


def test_restart_does_not_duplicate_existing_automatic_runtime_session(
    tmp_path: Path,
) -> None:
    finding = _finding(tmp_path)

    first_engine = _engine(tmp_path, finding)
    created = first_engine._prepare_automatic_runtime_failure_entry(finding)
    assert created is not None

    restarted_engine = _engine(tmp_path, finding)
    duplicate = restarted_engine._prepare_automatic_runtime_failure_entry(finding)

    assert duplicate is None
    restored = restarted_engine._active_self_repair_session()
    assert restored is not None
    assert restored.session_id == created.session_id
    assert restored.plan_id == created.plan_id


def test_restart_marks_runtime_repair_stale_when_source_changed(
    tmp_path: Path,
) -> None:
    finding = _finding(tmp_path)

    first_engine = _engine(tmp_path, finding, fingerprint="source-before-restart")
    created = first_engine._prepare_automatic_runtime_failure_entry(finding)
    assert created is not None
    assert created.state == "planned"

    restarted_engine = _engine(tmp_path, finding, fingerprint="source-after-restart")
    restored = restarted_engine._active_self_repair_session()

    assert restored is None
    stored = restarted_engine._self_repair_store().load()
    assert stored is not None
    assert stored.state == "stale"
    assert stored.finding_id == finding.finding_id
    assert stored.approved_paths == ("core/assistant.py",)
    assert stored.approved_symbols == ("AssistantEngine.handle",)
    assert "değişti" in stored.last_error


def test_completed_runtime_repair_is_not_restored_as_active_after_restart(
    tmp_path: Path,
) -> None:
    finding = _finding(tmp_path)
    first_engine = _engine(tmp_path, finding)
    created = first_engine._prepare_automatic_runtime_failure_entry(finding)
    assert created is not None

    first_engine._self_repair_store().transition(
        "completed",
        expected={"planned"},
    )

    restarted_engine = _engine(tmp_path, finding)
    assert restarted_engine._active_self_repair_session() is None

    stored = restarted_engine._self_repair_store().load()
    assert stored is not None
    assert stored.state == "completed"
