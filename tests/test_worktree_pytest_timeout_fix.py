from pathlib import Path

def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")

def test_own_pytest_timeout_allows_full_regression():
    source = _source()
    start = source.index("def _run_own_tests")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]
    assert "timeout=1800" in block
    assert "timeout=300" not in block
    assert "Pytest otuz dakika" in block

def test_runtime_health_timeout_is_unchanged():
    source = _source()
    start = source.index("def _runtime_health_check")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]
    assert "timeout=60" in block
