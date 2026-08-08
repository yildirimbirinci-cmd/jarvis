from pathlib import Path
from types import SimpleNamespace
import os

from artmach_assistant.core.assistant import AssistantEngine


def _engine(cycle):
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    state = {"cycle": dict(cycle)}

    def load():
        return dict(state["cycle"])

    def save(stage, detail, **kwargs):
        state["cycle"] = {
            "version": 4,
            "stage": stage,
            "detail": detail,
            "failures": list(kwargs.get("failures", [])),
            "attempt": int(kwargs.get("attempt", 0) or 0),
            "changed_paths": list(kwargs.get("changed_paths", [])),
            "validation_summary": kwargs.get("validation_summary", ""),
            "version_summary": kwargs.get("version_summary", ""),
            "owner_pid": os.getpid(),
        }

    engine._load_own_code_cycle = load
    engine._save_own_code_cycle = save
    return engine, state


def test_restart_interrupts_isolated_validation_without_claiming_live_apply():
    engine, state = _engine({
        "version": 4,
        "stage": "isolated_validation",
        "detail": "worktree running",
        "failures": [],
        "attempt": 0,
        "changed_paths": ["core/assistant.py"],
        "owner_pid": os.getpid() + 10000,
    })

    result = engine.own_code_cycle_report()

    assert state["cycle"]["stage"] == "interrupted_validation"
    assert state["cycle"]["changed_paths"] == ["core/assistant.py"]
    assert "worktree doğrulaması kesildi" in result


def test_restart_marks_applying_as_recovery_required():
    engine, state = _engine({
        "version": 4,
        "stage": "applying",
        "detail": "apply running",
        "failures": [],
        "attempt": 0,
        "changed_paths": ["core/assistant.py"],
        "owner_pid": os.getpid() + 10000,
    })

    result = engine.own_code_cycle_report()

    assert state["cycle"]["stage"] == "recovery_required"
    assert state["cycle"]["changed_paths"] == ["core/assistant.py"]
    assert "kaynak doğrulaması gerekiyor" in result


def test_restart_marks_validating_as_recovery_required():
    engine, state = _engine({
        "version": 4,
        "stage": "validating",
        "detail": "tests running",
        "failures": [],
        "attempt": 0,
        "changed_paths": ["core/assistant.py"],
        "owner_pid": os.getpid() + 10000,
    })

    engine.own_code_cycle_report()

    assert state["cycle"]["stage"] == "recovery_required"


def test_same_process_active_state_is_not_reclassified():
    engine, state = _engine({
        "version": 4,
        "stage": "isolated_validation",
        "detail": "worktree running",
        "failures": [],
        "attempt": 0,
        "changed_paths": ["core/assistant.py"],
        "owner_pid": os.getpid(),
    })

    result = engine.own_code_cycle_report()

    assert state["cycle"]["stage"] == "isolated_validation"
    assert "geçici worktree" in result


def test_intermediate_states_persist_target_paths_and_owner_pid():
    source = (
        Path(__file__).resolve().parents[1] / "core" / "assistant.py"
    ).read_text(encoding="utf-8")

    assert '"owner_pid": os.getpid()' in source
    assert source.count("changed_paths=cycle_paths") >= 3
    assert "changed_paths=tuple(" in source
