from pathlib import Path

def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")

def test_own_pytest_runs_from_validation_root():
    source = _source()
    start = source.index("def _run_own_tests")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]
    assert 'command = [sys.executable, "-m", "pytest", "-q", str(tests)]' in block
    assert 'cwd=str(root)' in block
    assert 'cwd=str(root.parent)' not in block

def test_runtime_health_still_runs_from_package_parent():
    source = _source()
    start = source.index("def _runtime_health_check")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]
    assert 'cwd=str(root.parent)' in block
