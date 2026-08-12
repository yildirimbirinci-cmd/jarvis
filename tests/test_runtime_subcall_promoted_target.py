from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


class Store:
    def __init__(self, override):
        self.override = override

    def load(self, finding_id):
        return self.override


def _engine(override, fingerprint="fp-current"):
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_research_context = {
        "finding_id": "RUN-06578E9EDE",
    }
    engine.last_action_context = {}
    finding = SimpleNamespace(
        finding_id="RUN-06578E9EDE",
        affected_paths=("core/task_orchestrator.py",),
        affected_symbols=("TaskOrchestrator.wrap.execute",),
    )
    engine._find_runtime_finding = lambda finding_id: finding
    engine.runtime_target_override_store = Store(override)
    engine._current_source_fingerprint = lambda path: fingerprint
    return engine


def _override(source_fingerprint="fp-current"):
    return SimpleNamespace(
        source_path="core/assistant.py",
        symbol="AssistantEngine.handle",
        source_fingerprint=source_fingerprint,
    )


def test_subcall_measurement_uses_valid_promoted_target():
    engine = _engine(_override())
    result = engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE alt cagri olcumu yap"
    )
    assert "Hedef: core/assistant.py - AssistantEngine.handle" in result
    assert "TaskOrchestrator.wrap.execute" not in result


def test_subcall_measurement_falls_back_when_override_is_stale():
    engine = _engine(_override("old-fp"), fingerprint="new-fp")
    result = engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE alt cagri olcumu yap"
    )
    assert (
        "Hedef: core/task_orchestrator.py - TaskOrchestrator.wrap.execute"
        in result
    )


def test_subcall_measurement_falls_back_when_no_override_exists():
    engine = _engine(None)
    result = engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE alt cagri olcumu yap"
    )
    assert (
        "Hedef: core/task_orchestrator.py - TaskOrchestrator.wrap.execute"
        in result
    )
