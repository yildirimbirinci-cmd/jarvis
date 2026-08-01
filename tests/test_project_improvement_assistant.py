from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.project_improvement_runtime import (
    FindingImplementationContext,
)
from artmach_assistant.core.project_improvement_service import (
    ImprovementEvidence,
    ImprovementFinding,
    ProjectImprovementAssessment,
    ProjectProfile,
)


class _Config:
    model = "legacy"
    chat_model = "chat"
    code_model = "coder"
    ollama_url = "http://127.0.0.1:11434"
    internet_research_enabled = False

    def __init__(self) -> None:
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


def _finding() -> ImprovementFinding:
    return ImprovementFinding(
        finding_id="ARC-123456789A",
        severity="high",
        category="dependency_cycle",
        title="Döngüsel bağımlılık",
        explanation="a.py ile b.py birbirini içe aktarıyor.",
        confidence=0.91,
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
        recommendation="Bağımlılık yönünü tek yöne çevir.",
        acceptance_criteria=("Döngü kalmamalı.", "Testler geçmeli."),
        research_query="Python dependency cycle official guidance",
    )


def _assessment(root: Path) -> ProjectImprovementAssessment:
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
        findings=(_finding(),),
        scanned_files=3,
    )


def _context(root: Path, *, own_code: bool) -> FindingImplementationContext:
    return FindingImplementationContext(
        assessment=_assessment(root),
        finding=_finding(),
        own_code=own_code,
        evidence_text=(
            "BULGU: ARC-123456789A\nKANIT:\n- a.py: a.py -> b.py\n"
            "BAŞARI ÖLÇÜTLERİ:\n- Döngü kalmamalı.\n- Testler geçmeli."
        ),
        research_text="[S1] official guidance",
    )


def test_architecture_research_permission_resumes_specialized_flow() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = _Config()
    engine.pending_research_query = ""
    engine.pending_research_mode = "project_improvement"
    engine.pending_research_own_code = True
    engine.dialogue_active = False
    engine.research_project_improvements = (
        lambda *, own_code=False: f"SPECIALIZED:{own_code}"
    )

    answer = engine._internet_research_request(
        "internet araştırmasına izin ver"
    )

    assert answer == "SPECIALIZED:True"
    assert engine.config.internet_research_enabled is True
    assert engine.pending_research_mode == ""
    assert engine.pending_research_own_code is False


def test_project_improvement_router_distinguishes_own_code() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    calls: list[tuple[bool, bool]] = []
    engine.project_improvement_report = (
        lambda *, own_code=False, refresh=True: calls.append((own_code, refresh))
        or "REPORT"
    )

    answer = engine._project_improvement_request(
        "Kendi kod mimarindeki hata ve eksikleri bul"
    )

    assert answer == "REPORT"
    assert calls == [(True, True)]


def test_external_finding_prepares_but_does_not_apply_project_edit(tmp_path) -> None:
    captured: dict[str, object] = {}
    proposal = EditProposal(
        "cycle fix",
        [ProposedFileChange("a.py", "reason", "old", "new", True)],
    )

    class _Runtime:
        last_assessment = _assessment(tmp_path)
        has_pending_project_edit = False

        def implementation_context(self, finding_id: str):
            assert finding_id == "ARC-123456789A"
            return _context(tmp_path, own_code=False)

        def prepare_edit(self, instruction: str, **kwargs):
            captured["instruction"] = instruction
            captured.update(kwargs)
            return proposal

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.project_improvements = _Runtime()

    answer = engine.prepare_improvement_implementation("ARC-123456789A")

    assert "proje taslağını uygula" in answer
    assert captured["approved_paths"] == ("a.py", "b.py")
    assert "a.py -> b.py" in str(captured["evidence_context"])
    assert "official guidance" in str(captured["research_context"])


def test_own_finding_uses_existing_own_code_plan_workflow(tmp_path) -> None:
    class _Runtime:
        last_assessment = _assessment(tmp_path)
        has_pending_project_edit = False

        def implementation_context(self, finding_id: str):
            assert finding_id == "ARC-123456789A"
            return _context(tmp_path, own_code=True)

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.project_improvements = _Runtime()
    captured: list[str] = []
    engine.prepare_own_code_plan = (
        lambda instruction: captured.append(instruction) or "PLAN"
    )

    answer = engine.prepare_improvement_implementation("ARC-123456789A")

    assert answer == "PLAN"
    assert "ARC-123456789A" in captured[0]
    assert "Döngü kalmamalı" in captured[0]
    assert "GÜVENİLMEYEN İNTERNET" in captured[0]


def test_explicit_project_approval_delegates_to_project_runtime() -> None:
    class _Runtime:
        has_pending_project_edit = True

        def apply_pending(self) -> str:
            return "APPLIED"

        def reject_pending(self) -> str:
            return "REJECTED"

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.project_improvements = _Runtime()

    assert engine._project_edit_approval_request("proje taslağını uygula") == "APPLIED"
    assert engine._project_edit_approval_request("proje taslağını reddet") == "REJECTED"


def test_generic_approval_cannot_apply_selected_project_proposal() -> None:
    class _Runtime:
        has_pending_project_edit = True

        def reject_pending(self) -> str:
            return "REJECTED"

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.project_improvements = _Runtime()
    engine.editor = SimpleNamespace(pending=object())

    answer = engine._own_code_approval_request("evet")

    assert "seçili projeye ait" in answer
    assert "proje taslağını uygula" in answer


def test_own_code_proposal_does_not_overwrite_selected_project_draft() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.project_improvements = SimpleNamespace(has_pending_project_edit=True)

    answer = engine.prepare_own_code_proposal("bir fonksiyonu iyileştir")

    assert "Seçili proje için bekleyen" in answer


def test_project_improvement_router_accepts_plain_own_code_problem_request() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    calls: list[tuple[bool, bool]] = []
    engine.project_improvement_report = (
        lambda *, own_code=False, refresh=True: calls.append((own_code, refresh))
        or "REPORT"
    )

    answer = engine._project_improvement_request(
        "Kendi kodundaki hatalari ve eksikleri bul"
    )

    assert answer == "REPORT"
    assert calls == [(True, True)]


def test_project_improvement_router_accepts_selected_project_problem_request() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    calls: list[tuple[bool, bool]] = []
    engine.project_improvement_report = (
        lambda *, own_code=False, refresh=True: calls.append((own_code, refresh))
        or "REPORT"
    )

    answer = engine._project_improvement_request(
        "Secili projedeki hata ve eksikleri bul"
    )

    assert answer == "REPORT"
    assert calls == [(False, True)]


def test_spoken_architecture_report_is_concise_and_actionable() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    report = (
        "KANITA DAYALI M\u0130MAR\u0130 \u0130Y\u0130LE\u015eT\u0130RME RAPORU\n"
        "\u0130ncelenen dosya: 42 | Bulgu: 3\n\n"
        "[ARC-123456789A] HIGH - Dongusel bagimlilik\n"
        "Kanit: core/a.py:12: a -> b"
    )

    spoken = engine.spoken_response(report)

    assert "k\u0131rk iki dosya" in spoken
    assert "\u00fc\u00e7 kan\u0131tlanm\u0131\u015f iyile\u015ftirme aday\u0131" in spoken
    assert "ARC kimli\u011fini" in spoken
    assert "core/a.py" not in spoken
