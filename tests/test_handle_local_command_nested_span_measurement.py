from types import SimpleNamespace
from artmach_assistant.core.assistant import AssistantEngine

class Service:
    def __init__(self, events):
        self.events = tuple(events)
    def recent(self, **kwargs):
        return self.events

def _event(action, ms, parent_action):
    return SimpleNamespace(
        status="completed",
        action=action,
        duration_ms=ms,
        metadata={
            "parent_action": parent_action,
            "parent_correlation_id": "corr-1",
            "turn_id": "turn-1",
        },
    )

def test_executor_prefers_deeper_local_command_spans():
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_measurement_context = {"finding_id": "RUN-1"}
    engine.active_runtime_research_context = {}
    engine.last_action_context = {}
    engine._find_runtime_finding = lambda finding_id: SimpleNamespace(finding_id=finding_id)
    engine._development_root = lambda own_code=True: "."
    engine._runtime_event_service = lambda: Service([
        _event("handle_local_command", 23000.0, "handle_command"),
        _event("internet_research_request", 22000.0, "handle_local_command"),
        _event("maintenance_request", 20.0, "handle_local_command"),
    ])
    result = engine._runtime_subcall_measurement_execution_request(
        "Bu ALT CAGRI OLCUM PLANI'ni simdi uygula ve runtime event trace kayitlarini analiz et."
    )
    assert "Durum: MEASURED" in result
    assert "Dominant bounded cagri: internet_research_request" in result
    assert "handle_local_command: ortanca 23000" not in result
