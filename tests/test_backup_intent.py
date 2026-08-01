from artmach_assistant.core.intent_router import IntentKind, IntentRouter


def test_backup_is_classified_as_local_command() -> None:
    decision = IntentRouter().classify("kendi kaynak kodlarını yedekle")
    assert decision.kind is IntentKind.LOCAL_COMMAND
