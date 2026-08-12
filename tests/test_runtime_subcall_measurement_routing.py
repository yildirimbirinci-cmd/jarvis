from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine():
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_research_context = {
        "finding_id": "RUN-06578E9EDE",
    }
    engine.last_action_context = {}
    finding = SimpleNamespace(
        finding_id="RUN-06578E9EDE",
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
    )
    engine._find_runtime_finding = lambda finding_id: finding
    return engine


def test_subcall_measurement_intent_is_not_generic_finding_report():
    engine = _engine()
    result = engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE icin AssistantEngine.handle hedefinde "
        "22 benzersiz cagri icinden sureyi domine eden cagri veya cagri grubunu kanitla. "
        "Gerekirse dar kapsamli olcum plani uret. Patch uretme."
    )
    assert result is not None
    assert "ALT CAGRI OLCUM PLANI" in result
    assert "MEASUREMENT_REQUIRED" in result
    assert "Patch izni: hayir" in result
    assert "Kaynak kodu degistirilmedi" in result


def test_subcall_measurement_never_guesses_missing_target():
    engine = _engine()
    finding = SimpleNamespace(
        finding_id="RUN-06578E9EDE",
        affected_paths=(),
        affected_symbols=(),
    )
    engine._find_runtime_finding = lambda finding_id: finding
    result = engine._runtime_subcall_measurement_request(
        "RUN-06578E9EDE alt cagri olcumu yap"
    )
    assert "yapilandirilmis hedef yok" in result
    assert "Hedef tahmin edilmedi" in result
