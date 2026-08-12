from pathlib import Path


def test_assistant_routes_general_research_through_runtime_service():
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(
        encoding="utf-8"
    )
    assert "ResearchRuntimeService" in source
    assert "self.research_runtime.execute(" in source
    assert "resolve_research_command(" in source
    assert "parse_research_request(text)" in source
    assert "normalized.split(marker, 1)" not in source


def test_pending_permission_keeps_full_research_command():
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(
        encoding="utf-8"
    )
    assert 'self.pending_research_query = str(text or "").strip()' in source
    assert "return self._execute_general_research_command(pending_command)" in source
