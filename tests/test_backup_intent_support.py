from artmach_assistant.core.backup_intent_support import extract_backup_destination, is_backup_cancel


def test_extracts_windows_path_from_follow_up():
    assert extract_backup_destination(r"C:\Users\yildi\Desktop\Jarvis_yedek") == r"C:\Users\yildi\Desktop\Jarvis_yedek"


def test_extracts_quoted_destination_from_command():
    assert extract_backup_destination('projeyi "D:\\Backups\\Jarvis" klasörüne yedekle') == r"D:\Backups\Jarvis"


def test_non_path_request_remains_empty():
    assert extract_backup_destination("kendi kaynak kodlarını yedekle") == ""


def test_cancel_words_are_normalized():
    assert is_backup_cancel("  HAYIR  ") is True
