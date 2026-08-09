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


def test_recovery_rolls_back_interrupted_apply_before_marking_recovered(
    monkeypatch, tmp_path: Path,
):
    target = tmp_path / "core" / "assistant.py"
    target.parent.mkdir(parents=True)
    target.write_text("changed", encoding="utf-8")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    calls = []
    engine.own_code_transactions = SimpleNamespace(
        recover_incomplete=lambda: calls.append("recover") or "checkpoint recovered",
        undo=lambda: calls.append("undo") or "rollback complete",
    )
    statuses = iter([
        SimpleNamespace(returncode=0, stdout=" M core/assistant.py\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
    ])
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: next(statuses))

    ok, detail = engine._verify_interrupted_engineering_recovery({
        "changed_paths": ["core/assistant.py"],
    })

    assert ok is True
    assert calls == ["recover", "undo"]
    assert "baseline" in detail


def test_recovery_never_rolls_back_changes_outside_interrupted_scope(
    monkeypatch, tmp_path: Path,
):
    target = tmp_path / "core" / "assistant.py"
    target.parent.mkdir(parents=True)
    target.write_text("changed", encoding="utf-8")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine.own_code_transactions = SimpleNamespace(
        recover_incomplete=lambda: (_ for _ in ()).throw(AssertionError()),
        undo=lambda: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=" M core/assistant.py\n M core/unrelated.py\n",
            stderr="",
        ),
    )

    ok, detail = engine._verify_interrupted_engineering_recovery({
        "changed_paths": ["core/assistant.py"],
    })

    assert ok is False
    assert "core/unrelated.py" in detail
