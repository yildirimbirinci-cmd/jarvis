from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


class Service:
    def __init__(self, events):
        self.events = tuple(events)

    def recent(self, **kwargs):
        return self.events


def _event(action: str, duration_ms: float):
    return SimpleNamespace(
        status="completed",
        action=action,
        duration_ms=duration_ms,
        metadata={
            "parent_action": "dialogue_respond",
            "parent_correlation_id": "corr-1",
            "turn_id": "turn-1",
        },
    )


def _engine():
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_measurement_context = {
        "finding_id": "RUN-06578E9EDE",
    }
    engine.active_runtime_research_context = {}
    engine.last_action_context = {}
    engine._find_runtime_finding = lambda finding_id: SimpleNamespace(
        finding_id=finding_id
    )
    engine._development_root = lambda own_code=True: "."
    engine._runtime_event_service = lambda: Service(
        [
            _event("dialogue_respond_context_prepare", 2.0),
            _event("dialogue_respond_ollama_chat", 1400.0),
            _event("dialogue_respond_output_process", 0.2),
        ]
    )
    return engine


def test_live_dialogue_respond_measurement_phrase_routes_to_executor():
    engine = _engine()

    result = engine._runtime_subcall_measurement_execution_request(
        "RUN-06578E9EDE icin dialogue_respond alt olcumunu simdi uygula. "
        "Mevcut bounded nested-span kayitlarini analiz et ve context hazirligi, "
        "ollama_chat ve cikti isleme asamalarindan hangisinin sureyi domine "
        "ettigini kanitla. Patch uretme."
    )

    assert result is not None
    assert "Durum: MEASURED" in result
    assert "Dominant bounded cagri: dialogue_respond_ollama_chat" in result


def test_dialogue_respond_execution_route_precedes_generic_runtime_report():
    import inspect

    source = inspect.getsource(AssistantEngine.handle_local_command)
    execute = "self._runtime_subcall_measurement_execution_request(text)"
    generic = "self._targeted_runtime_finding_report_request(text)"
    assert source.index(execute) < source.index(generic)
