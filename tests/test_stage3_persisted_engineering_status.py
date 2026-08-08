from pathlib import Path

def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")

def test_cycle_schema_persists_restart_safe_engineering_fields():
    source = _source()
    start = source.index("def _save_own_code_cycle")
    end = source.index("\n    @staticmethod", start + 10)
    block = source[start:end]
    assert '"version": 4' in block
    assert '"changed_paths"' in block
    assert '"validation_summary"' in block
    assert '"version_summary"' in block
    assert '"updated_at"' in block

def test_cycle_loader_keeps_v3_backward_compatibility_and_v4():
    source = _source()
    start = source.index("def _load_own_code_cycle")
    end = source.index("\n    @staticmethod", start + 10)
    block = source[start:end]
    assert 'data.get("version") in {3, 4}' in block

def test_completed_apply_persists_paths_validation_and_version():
    source = _source()
    start = source.index("completed_paths = (")
    end = source.index("\n        baseline_note =", start)
    block = source[start:end]
    assert "approved_proposal.files" in block
    assert "changed_paths=completed_paths" in block
    assert "validation_summary=(" in block
    assert "version_summary=version_report" in block

def test_cycle_report_renders_persisted_fields_after_restart():
    source = _source()
    start = source.index("def own_code_cycle_report")
    end = source.index("\n    def _own_code_cycle_request", start)
    block = source[start:end]
    assert 'cycle.get("changed_paths", ())' in block
    assert 'cycle.get("validation_summary", "")' in block
    assert 'cycle.get("version_summary", "")' in block

def test_explicit_engineering_status_route_precedes_other_handlers():
    source = _source()
    start = source.index("def handle_local_command")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]
    exact = block.index('"kendi kod gelistirme durumu"')
    patch = block.index("patch_session_command = self._patch_session_command_request(text)")
    assert exact < patch
    assert "return self.own_code_cycle_report()" in block[exact:patch]
