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


def _healthy_recovery(engine):
    engine._compile_own_code = lambda: (True, "compile ok")
    engine._runtime_health_check = lambda: (True, "runtime ok")
    engine._run_own_tests = lambda: (True, "tests ok")
    engine._test_failure_ids = lambda _output: set()
    engine._save_own_validation = lambda *_args: None


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


def test_restart_marks_user_rollback_as_recovery_required():
    engine, state = _engine({
        "version": 4,
        "stage": "rolling_back",
        "detail": "rollback validation running",
        "failures": [],
        "attempt": 0,
        "changed_paths": ["core/assistant.py"],
        "owner_pid": os.getpid() + 10000,
    })

    result = engine.own_code_cycle_report()

    assert state["cycle"]["stage"] == "recovery_required"
    assert state["cycle"]["changed_paths"] == ["core/assistant.py"]
    assert "kaynak doğrulaması gerekiyor" in result


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
    _healthy_recovery(engine)
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
    assert "regresyon" in detail


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


def test_clean_recovery_is_not_completed_without_health_validation(
    monkeypatch, tmp_path: Path,
):
    target = tmp_path / "core" / "assistant.py"
    target.parent.mkdir(parents=True)
    target.write_text("baseline", encoding="utf-8")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine._compile_own_code = lambda: (True, "compile ok")
    engine._runtime_health_check = lambda: (True, "runtime ok")
    engine._run_own_tests = lambda: (
        False,
        "FAILED tests/test_new.py::test_regression - failed",
    )
    engine._test_failure_ids = lambda _output: {
        "tests/test_new.py::test_regression"
    }
    saved = []
    engine._save_own_validation = (
        lambda success, output: saved.append((success, output))
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )

    ok, detail = engine._verify_interrupted_engineering_recovery({
        "changed_paths": ["core/assistant.py"],
        "failures": [],
    })

    assert ok is False
    assert "regresyon doğrulaması başarısız" in detail
    assert saved[-1][0] is False


def test_clean_recovery_accepts_only_preexisting_test_failures(
    monkeypatch, tmp_path: Path,
):
    target = tmp_path / "core" / "assistant.py"
    target.parent.mkdir(parents=True)
    target.write_text("baseline", encoding="utf-8")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine._compile_own_code = lambda: (True, "compile ok")
    engine._runtime_health_check = lambda: (True, "runtime ok")
    engine._run_own_tests = lambda: (
        False,
        "FAILED tests/test_old.py::test_known - failed",
    )
    engine._test_failure_ids = lambda _output: {
        "tests/test_old.py::test_known"
    }
    engine._save_own_validation = lambda *_args: None
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )

    ok, detail = engine._verify_interrupted_engineering_recovery({
        "changed_paths": ["core/assistant.py"],
        "failures": ["tests/test_old.py::test_known"],
    })

    assert ok is True
    assert "regresyon karşılaştırması tamamlandı" in detail
