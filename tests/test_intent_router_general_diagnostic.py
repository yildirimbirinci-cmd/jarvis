from artmach_assistant.core.intent_router import IntentKind, IntentRouter


def test_general_problem_requests_route_to_diagnostic() -> None:
    router = IntentRouter()
    assert router.classify("Arayüz donuyor, sorunu gider").kind is IntentKind.DIAGNOSTIC
    assert router.classify("Git push sorununu çöz").kind is IntentKind.DIAGNOSTIC
    assert router.classify("Performans sorununu analiz et").kind is IntentKind.DIAGNOSTIC
    assert router.classify("Hafıza sorununu düzelt").kind is IntentKind.DIAGNOSTIC
