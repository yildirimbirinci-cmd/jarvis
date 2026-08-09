from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine(tmp_path: Path, cycle: dict):
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    engine.own_project_root = lambda: tmp_path
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
        }

    engine._load_own_code_cycle = load
    engine._save_own_code_cycle = save
    engine._compile_own_code = lambda: (True, "compile ok")
    engine._runtime_health_check = lambda: (True, "runtime ok")
    engine._run_own_tests = lambda: (True, "tests ok")
    engine._test_failure_ids = lambda _output: set()
    engine._save_own_validation = lambda *_args: None
    return engine, state


def _init_git_repo(root: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "jarvis@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Jarvis Test"],
        cwd=root,
        check=True,
    )
    (root / "core").mkdir()
    (root / "core" / "assistant.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_recovery_required_becomes_recovered_when_git_tree_is_clean(tmp_path):
    _init_git_repo(tmp_path)
    engine, state = _engine(
        tmp_path,
        {
            "version": 4,
            "stage": "recovery_required",
            "detail": "restart during apply",
            "failures": [],
            "attempt": 0,
            "changed_paths": ["core/assistant.py"],
            "validation_summary": "",
            "version_summary": "checkpoint-1",
        },
    )

    result = engine.own_code_cycle_report()

    assert state["cycle"]["stage"] == "recovered"
    assert state["cycle"]["changed_paths"] == ["core/assistant.py"]
    assert "kaynak durumu doğrulandı" in result
    assert "Git çalışma ağacı temiz" in result


def test_recovery_required_stays_blocked_when_tracked_tree_is_dirty(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "core" / "assistant.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    engine, state = _engine(
        tmp_path,
        {
            "version": 4,
            "stage": "recovery_required",
            "detail": "restart during validation",
            "failures": [],
            "attempt": 0,
            "changed_paths": ["core/assistant.py"],
            "validation_summary": "",
            "version_summary": "checkpoint-1",
        },
    )

    result = engine.own_code_cycle_report()

    assert state["cycle"]["stage"] == "recovery_required"
    assert "kaydedilmemiş tracked değişiklikler" in result


def test_recovery_required_stays_blocked_when_target_missing(tmp_path):
    _init_git_repo(tmp_path)
    engine, state = _engine(
        tmp_path,
        {
            "version": 4,
            "stage": "recovery_required",
            "detail": "restart during apply",
            "failures": [],
            "attempt": 0,
            "changed_paths": ["core/missing.py"],
            "validation_summary": "",
            "version_summary": "",
        },
    )

    result = engine.own_code_cycle_report()

    assert state["cycle"]["stage"] == "recovery_required"
    assert "Recovery hedef dosyaları eksik" in result


def test_recovered_label_and_verifier_are_present_in_source():
    source = (
        Path(__file__).resolve().parents[1] / "core" / "assistant.py"
    ).read_text(encoding="utf-8")

    assert "def _verify_interrupted_engineering_recovery" in source
    assert (
        '"recovered": "yarım uygulama sonrası kaynak durumu doğrulandı"'
        in source
    )
    assert (
        '["git", "status", "--porcelain=v1", "--untracked-files=no"]'
        in source
    )
