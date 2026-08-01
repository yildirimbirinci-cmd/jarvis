from artmach_assistant.core.backup_intent_support import is_backup_approval, is_backup_cancel


def test_backup_confirmation_requires_explicit_approval() -> None:
    assert is_backup_approval("yedeklemeyi onayla") is True
    assert is_backup_approval("Tamam") is True
    assert is_backup_approval("bir dakika") is False


def test_backup_confirmation_cancel_words_are_not_approvals() -> None:
    assert is_backup_cancel("iptal") is True
    assert is_backup_approval("iptal") is False
