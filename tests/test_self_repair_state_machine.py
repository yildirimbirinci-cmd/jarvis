from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.runtime_observability import RuntimeEvidence, RuntimeFinding
from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


def _finding(root: Path) -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-ABCDEF1234",
        severity="high",
        category="repeated_runtime_failure",
        title="Tekrarlanan hata: Worker.execute",
        explanation="Aynı hata üç kez oluştu.",
        confidence=0.95,
        occurrence_count=3,
        last_seen="2026-08-01T08:00:00+00:00",
        workspace=str(root),
        scope="own_code",
        affected_paths=("core/worker.py",),
        affected_symbols=("Worker.execute",),
        evidence=(RuntimeEvidence(
            event_id="EVT-1",
            created_at="2026-08-01T08:00:00+00:00",
            detail="RuntimeError: boom",
            source_path="core/worker.py",
            symbol="Worker.execute",
        ),),
        recommendation="En küçük üretim kodu düzeltmesini hazırla.",
        acceptance_criteria=("Hata tekrar etmemeli.",),
        research_query="",
    )


def _engine(tmp_path: Path) -> AssistantEngine:
    root = tmp_path / "artmach_assistant"
    (root / "core").mkdir(parents=True)
    (root / "core" / "worker.py").write_text(
        "class Worker:\n    def execute(self):\n        raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.self_repair_sessions = SelfRepairSessionStore(tmp_path / "self_repair.json")
    engine.own_project_root = lambda: root
    engine._development_root = lambda *, own_code: root
    engine._current_source_fingerprint = lambda: "SOURCE-1"
    engine._find_runtime_finding = lambda finding_id: (
        _finding(root) if finding_id == "RUN-ABCDEF1234" else None
    )
    engine._load_own_code_plan = lambda: None
    engine._save_own_code_plan = lambda _plan: None
    engine.own_code_history = SimpleNamespace(record=lambda *_a, **_k: None)
    return engine


def test_run_plan_survives_short_start_and_keeps_exact_scope(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    planned = engine._reserved_self_repair_request(
        "RUN-ABCDEF1234 bulgusunu düzelt"
    )
    assert "RPR-ABCDEF1234" in planned

    pending = EditProposal(
        "targeted",
        [ProposedFileChange(
            "core/worker.py", "fix", "old", "new", True
        )],
    )
    engine.editor = SimpleNamespace(pending=None, reject=lambda: None)
    captured: dict[str, object] = {}

    def prepare(instruction: str, **kwargs):
        captured["instruction"] = instruction
        captured.update(kwargs)
        engine.editor.pending = pending
        return "PROPOSAL_READY"

    engine.prepare_own_code_proposal = prepare
    response = engine._reserved_self_repair_request("başla")

    assert "PROPOSAL_READY" in response
    assert captured["approved_paths"] == ("core/worker.py",)
    assert captured["approved_symbols"] == ("Worker.execute",)
    assert captured["plan_id"] == "RPR-ABCDEF1234"
    assert engine.self_repair_sessions.load().state == "proposal_ready"


def test_bare_start_never_applies_ready_self_repair(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine._reserved_self_repair_request("RUN-ABCDEF1234 bulgusunu düzelt")
    session = engine.self_repair_sessions.transition(
        "generating", expected={"planned"}, increment_attempt=True
    )
    pending = EditProposal(
        "targeted",
        [ProposedFileChange("core/worker.py", "fix", "old", "new", True)],
    )
    from artmach_assistant.core.own_code_approval import proposal_fingerprint

    engine.self_repair_sessions.transition(
        "proposal_ready",
        expected={"generating"},
        proposal_fingerprint=proposal_fingerprint(pending),
    )
    engine.editor = SimpleNamespace(pending=pending, reject=lambda: None)
    engine.apply_pending_own_code_proposal = lambda: "SHOULD_NOT_APPLY"

    answer = engine._reserved_self_repair_request("başla")

    assert "taslak zaten hazır" in answer
    assert "SHOULD_NOT_APPLY" not in answer


def test_explicit_draft_approval_applies_only_matching_proposal(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine._reserved_self_repair_request("RUN-ABCDEF1234 bulgusunu düzelt")
    engine.self_repair_sessions.transition(
        "generating", expected={"planned"}, increment_attempt=True
    )
    pending = EditProposal(
        "targeted",
        [ProposedFileChange("core/worker.py", "fix", "old", "new", True)],
    )
    from artmach_assistant.core.own_code_approval import proposal_fingerprint

    engine.self_repair_sessions.transition(
        "proposal_ready",
        expected={"generating"},
        proposal_fingerprint=proposal_fingerprint(pending),
    )
    engine.editor = SimpleNamespace(pending=pending, reject=lambda: None)
    engine.apply_pending_own_code_proposal = lambda: (
        "Onayladığın kod değişikliği uygulandı; derleme doğrulamasından geçti."
    )

    answer = engine._reserved_self_repair_request("taslağı onayla")

    assert "uygulandı" in answer
    assert engine.self_repair_sessions.load().state == "completed"


def test_arbitrary_voice_text_does_not_fill_generic_plan_clarification(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module, "OWN_CODE_PLAN_FILE", tmp_path / "own_code_plan.json"
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.self_repair_sessions = SelfRepairSessionStore(tmp_path / "self_repair.json")
    question = engine.prepare_own_code_plan("Kendi kodlarını geliştir.")

    answer = engine._handle_own_code_plan_follow_up("Haa, ses işinizlanmış.")

    assert answer is None
    assert question.startswith("Geliştirme hedefi")
    assert engine._load_own_code_plan()["status"] == "needs_clarification"


def test_concrete_plan_clarification_still_works(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        assistant_module, "OWN_CODE_PLAN_FILE", tmp_path / "own_code_plan.json"
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.self_repair_sessions = SelfRepairSessionStore(tmp_path / "self_repair.json")
    engine.own_project_root = lambda: tmp_path
    engine.workspace = SimpleNamespace(
        set_workspace=lambda _root: None,
        call_graph_patch_context=lambda *_a, **_k: SimpleNamespace(text=""),
    )
    engine.prepare_own_code_plan("Kendi kodlarını geliştir.")

    answer = engine._handle_own_code_plan_follow_up(
        "Sesli cevap gecikmesi iki saniyenin altına insin."
    )

    assert "planı durdurdum" in answer
    assert engine._load_own_code_plan()["status"] == "needs_scope"
