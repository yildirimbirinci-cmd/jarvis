from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_command_router import (
    OwnCodeAction,
    classify_own_code_command,
)

PROMPT = (
    "Aşama 3 restart recovery denetimi yap. "
    "Mevcut persistent engineering durumunu salt-okunur incele. "
    "FAILED, COMPLETED, STALE veya ROLLED_BACK terminal kayıtları recovery "
    "adayı yapma. Yalnız gerçekten yarım kalmış ve devam ettirilebilir bir "
    "engineering işlemi varsa bildir. Yeni research, proposal, patch, "
    "validation, apply, rollback veya recovery başlatma."
)

def test_restart_recovery_prompt_is_read_only_engineering_state() -> None:
    assert AssistantEngine._asks_for_engineering_state_only(PROMPT) is True

def test_structured_router_classifies_restart_recovery_as_state_report() -> None:
    command = classify_own_code_command(PROMPT)
    assert command.action is OwnCodeAction.REPORT_ENGINEERING_STATE
    assert command.read_only is True
    assert command.apply is False
