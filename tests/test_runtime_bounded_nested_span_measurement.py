from types import SimpleNamespace
from artmach_assistant.core.assistant import AssistantEngine

class Service:
    def __init__(self, events):
        self.events = tuple(events)
    def recent(self, **kwargs):
        return self.events

def _engine(events):
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_measurement_context = {"finding_id": "RUN-06578E9EDE"}
    engine.active_runtime_research_context = {}
    engine.last_action_context = {}
    finding = SimpleNamespace(finding_id="RUN-06578E9EDE")
    engine._find_runtime_finding = lambda finding_id: finding
    engine._runtime_event_service = lambda: Service(events)
    engine._development_root = lambda own_code=True: "."
    return engine

def _event(action, ms):
    return SimpleNamespace(
        status="completed",
        action=action,
        duration_ms=ms,
        metadata={
            "parent_action": "handle_command",
            "parent_correlation_id": "parent-1",
            "turn_id": "turn-1",
        },
    )

def test_nested_span_execution_measures_dominant_bounded_child():
    engine = _engine([
        _event("handle_local_command", 1800.0),
        _event("dialogue_remember", 10.0),
        _event("spoken_response", 30.0),
    ])
    result = engine._runtime_subcall_measurement_execution_request(
        "Bu ALT CAGRI OLCUM PLANI'ni simdi uygula ve runtime event trace kayitlarini analiz et."
    )
    assert "Durum: MEASURED" in result
    assert "Dominant bounded cagri: handle_local_command" in result
    assert "instrument edilmemis alt cagrilar hakkinda iddia yapilmaz" in result

def test_no_correlated_sample_is_insufficient():
    engine = _engine([])
    result = engine._runtime_subcall_measurement_execution_request(
        "Bu ALT CAGRI OLCUM PLANI'ni simdi uygula ve runtime event trace kayitlarini analiz et."
    )
    assert "INSUFFICIENT_TRACE_DATA" in result
    assert "Dominant cagri uydurulmadi" in result
