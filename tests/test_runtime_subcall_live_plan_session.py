from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


class Store:
    def load(self, finding_id):
        return None


def _engine():
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_research_context = {"finding_id": "RUN-06578E9EDE"}
    engine.active_runtime_measurement_context = {}
    engine.last_action_context = {}
    engine._runtime_event_service = lambda: SimpleNamespace(recent=lambda **_kwargs: ())
    engine._development_root = lambda own_code=True: "."
    finding = SimpleNamespace(
        finding_id="RUN-06578E9EDE",
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
        evidence=(
            SimpleNamespace(
                action_duration_ms=2000.0,
                wrapper_overhead_ms=0.3,
            ),
        ),
    )
    engine._find_runtime_finding = lambda finding_id: finding
    engine.runtime_target_override_store = Store()
    engine._current_source_fingerprint = lambda path: ""
    return engine


def test_live_plan_creation_phrase_routes_to_measurement_plan():
    engine = _engine()
    result = engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE icin AssistantEngine.handle hedefinde "
        "alt cagri olcum planini olustur. Patch uretme ve hicbir dosyayi degistirme."
    )
    assert result is not None
    assert "ALT CAGRI OLCUM PLANI" in result
    assert engine.active_runtime_measurement_context["finding_id"] == "RUN-06578E9EDE"


def test_live_plan_then_execute_uses_saved_finding_id():
    engine = _engine()
    engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE icin AssistantEngine.handle hedefinde "
        "alt cagri olcum planini olustur."
    )
    engine.active_runtime_research_context = {}
    engine.last_action_context = {}

    result = engine._runtime_subcall_measurement_execution_request(
        "Bu ALT CAGRI OLCUM PLANI'ni simdi uygula. "
        "Mevcut runtime event trace kayitlarini ayni run turn kimlikleriyle analiz et."
    )

    assert "Bulgu: RUN-06578E9EDE" in result
    assert "INSUFFICIENT_TRACE_DATA" in result
    assert "BLOCKED" not in result
