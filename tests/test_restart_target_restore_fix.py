from pathlib import Path


def test_runtime_plan_refreshes_stale_promoted_target_before_scope_creation():
    root = Path(__file__).resolve().parents[1]
    source = (root / "core" / "assistant.py").read_text(encoding="utf-8")
    start = source.index("def prepare_runtime_improvement_implementation")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    refresh = (
        'if finding.category == "repeated_slow_operation":\n'
        '            finding, _repair_decision, _target_validation = (\n'
        '                self._assess_runtime_repair_with_target_refresh(finding)\n'
        '            )'
    )
    assert block.count(refresh) == 1
    assert block.index(refresh) < block.index("approved_paths = [")


def test_runtime_target_refresh_remains_source_fingerprint_aware():
    root = Path(__file__).resolve().parents[1]
    source = (root / "core" / "assistant.py").read_text(encoding="utf-8")
    start = source.index("def _assess_runtime_repair_with_target_refresh")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    assert "BLOCKED_WRONG_TARGET" in block
    assert "self._runtime_finding_local_validation(finding)" in block
    assert "self._find_runtime_finding(finding.finding_id)" in block
