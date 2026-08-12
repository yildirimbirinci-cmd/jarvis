from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


class Store:
    def __init__(self, override=None):
        self.override = override

    def load(self, finding_id):
        return self.override


def _engine():
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_research_context = {"finding_id": "RUN-06578E9EDE"}
    engine.last_action_context = {}
    engine._runtime_event_service = lambda: SimpleNamespace(recent=lambda **_kwargs: ())
    engine._development_root = lambda own_code=True: "."
    engine.active_runtime_measurement_context = {}
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
    engine.runtime_target_override_store = Store(None)
    engine._current_source_fingerprint = lambda path: ""
    return engine


def test_measurement_plan_stores_handoff_context():
    engine = _engine()
    result = engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE icin alt cagri olcumu yap"
    )
    assert "ALT CAGRI OLCUM PLANI" in result
    assert engine.active_runtime_measurement_context == {
        "kind": "runtime_subcall_measurement",
        "finding_id": "RUN-06578E9EDE",
        "target_path": "core/assistant.py",
        "target_symbol": "AssistantEngine.handle",
    }


def test_execute_uses_measurement_session_when_run_id_is_omitted():
    engine = _engine()
    engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE icin alt cagri olcumu yap"
    )
    engine.active_runtime_research_context = {}
    engine.last_action_context = {}

    result = engine._runtime_subcall_measurement_execution_request(
        "Bu ALT CAGRI OLCUM PLANI'ni simdi uygula. "
        "Mevcut runtime event trace kayitlarini analiz et."
    )

    assert result is not None
    assert "Bulgu: RUN-06578E9EDE" in result
    assert "INSUFFICIENT_TRACE_DATA" in result
    assert "BLOCKED" not in result


def test_wrong_or_missing_measurement_session_stays_blocked():
    engine = _engine()
    engine.active_runtime_measurement_context = {}
    engine.active_runtime_research_context = {}
    engine.last_action_context = {}

    result = engine._runtime_subcall_measurement_execution_request(
        "Bu ALT CAGRI OLCUM PLANI'ni simdi uygula. "
        "Mevcut runtime event trace kayitlarini analiz et."
    )

    assert "Durum: BLOCKED" in result
