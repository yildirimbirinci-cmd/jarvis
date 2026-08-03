from artmach_assistant.core.intent_router import IntentKind, IntentRouter


def test_voice_repair_routes_to_diagnostic_before_code_change() -> None:
    decision = IntentRouter().classify("Jarvis, ses sorunlarını gider")
    assert decision.kind is IntentKind.DIAGNOSTIC
    assert "kök neden" in decision.start_message


def test_specific_voice_fault_routes_to_diagnostic() -> None:
    assert IntentRouter().classify("Piper hatasını düzelt").kind is IntentKind.DIAGNOSTIC
