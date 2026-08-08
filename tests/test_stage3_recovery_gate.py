from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine(stage: str):
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=object())
    engine._load_own_code_cycle = lambda: {
        "version": 4,
        "stage": stage,
        "detail": "restart during apply",
        "changed_paths": ["core/assistant.py"],
        "validation_summary": "tracked source requires recovery",
    }
    return engine


def test_recovery_gate_reports_blocked_target_and_reason():
    engine = _engine("recovery_required")
    result = engine._own_code_recovery_gate()
    assert "recovery dogrulamasi tamamlanmali" in result
    assert "core/assistant.py" in result
    assert "tracked source requires recovery" in result


def test_recovered_state_does_not_block_new_work():
    engine = _engine("recovered")
    assert engine._own_code_recovery_gate() == ""


def test_apply_core_entry_checks_recovery_gate_before_pending_apply():
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")
    start = source.index("def apply_pending_own_code_proposal")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]
    assert block.index("recovery_gate = self._own_code_recovery_gate()") < block.index("if self.editor.pending is None:")


def test_proposal_core_entry_checks_recovery_gate_before_context_work():
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")
    start = source.index("def prepare_own_code_proposal")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]
    assert block.index("recovery_gate = self._own_code_recovery_gate()") < block.index('project_runtime = getattr(self, "project_improvements", None)')


def test_cycle_resume_does_not_start_repair_when_recovery_is_required():
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")
    start = source.index("def _own_code_cycle_request")
    end = source.index("\n    @staticmethod", start + 10)
    block = source[start:end]
    recovery = block.index('if stage == "recovery_required":')
    repair = block.index('if stage in {"analyzing", "proposal_failed", "rolled_back",')
    assert recovery < repair
    assert "return self.own_code_cycle_report()" in block[recovery:repair]
