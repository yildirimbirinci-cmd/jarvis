from pathlib import Path


def test_recovery_revision_lookup_is_lazy_for_legacy_cycles():
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(
        encoding="utf-8"
    )
    verify_start = source.index("    def _verify_interrupted_engineering_recovery(")
    validate_start = source.index("    def _validate_recovered_engineering_source(")
    report_start = source.index("    def own_code_cycle_report(", validate_start)
    verify = source[verify_start:validate_start]
    validate = source[validate_start:report_start]

    assert 'self._current_own_code_revision(root) if expected_revision else ""' in verify
    assert validate.count(
        'self._current_own_code_revision() if expected_revision else ""'
    ) == 2
