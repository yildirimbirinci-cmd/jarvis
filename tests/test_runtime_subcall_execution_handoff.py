from types import SimpleNamespace
from artmach_assistant.core.assistant import AssistantEngine


def _engine():
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_research_context = {"finding_id": "RUN-06578E9EDE"}
    engine.last_action_context = {}
    engine._runtime_event_service = lambda: SimpleNamespace(recent=lambda **_kwargs: ())
    engine._development_root = lambda own_code=True: "."
    evidence = (
        SimpleNamespace(
            action_duration_ms=2149.0,
            wrapper_overhead_ms=0.3,
        ),
    )
    finding = SimpleNamespace(
        finding_id="RUN-06578E9EDE",
        evidence=evidence,
    )
    engine._find_runtime_finding = lambda finding_id: finding
    return engine


def test_execute_subcall_plan_reports_insufficient_trace_instead_of_replanning():
    engine = _engine()
    result = engine._runtime_subcall_measurement_execution_request(
        "Bu ALT CAGRI OLCUM PLANI'ni simdi uygula. "
        "Mevcut runtime event/trace kayitlarini ayni run/turn kimlikleriyle analiz et."
    )
    assert "ALT CAGRI OLCUM SONUCU" in result
    assert "INSUFFICIENT_TRACE_DATA" in result
    assert "Dominant cagri uydurulmadi" in result
    assert "Patch izni: hayir" in result


def test_execution_route_precedes_plan_route():
    import inspect
    source = inspect.getsource(AssistantEngine.handle_local_command)
    execute = "self._runtime_subcall_measurement_execution_request(text)"
    plan = "self._runtime_subcall_measurement_request(text)"
    assert execute in source
    assert plan in source
    assert source.index(execute) < source.index(plan)


def test_execute_subcall_plan_accepts_live_turkish_phrase():
    engine = _engine()
    result = engine._runtime_subcall_measurement_execution_request(
        "Bu ALT CAGRI OLCUM PLANI'ni simdi uygula. "
        "Mevcut runtime event/trace kayitlarini ayni run/turn kimlikleriyle "
        "analiz et ve AssistantEngine.handle icindeki sureyi domine eden "
        "alt cagri veya cagri grubunu kanitla. Planini tekrar anlatma."
    )
    assert result is not None
    assert "INSUFFICIENT_TRACE_DATA" in result
    assert "Dominant cagri uydurulmadi" in result


def test_measurement_plan_without_execute_verb_stays_plan_only():
    engine = _engine()
    result = engine._runtime_subcall_measurement_execution_request(
        "ALT CAGRI OLCUM PLANI nedir"
    )
    assert result is None
