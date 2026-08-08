from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine(cycle):
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._load_own_code_cycle = lambda: cycle
    return engine


def test_persisted_engineering_report_is_grounded_and_read_only():
    cycle = {
        "version": 4,
        "stage": "recovery_required",
        "attempt": 2,
        "detail": "restart interrupted validation",
        "changed_paths": ["core/assistant.py"],
        "validation_summary": "source verification pending",
    }
    engine = _engine(cycle)
    text = engine._persisted_engineering_state_report()
    assert "Stage: recovery_required" in text
    assert "Attempt: 2/3" in text
    assert "core/assistant.py" in text
    assert "source verification pending" in text
    assert "Recovery: Gerekli" in text


def test_own_code_cycle_wording_routes_to_engineering_state_not_git_only():
    engine = _engine({"version": 4, "stage": "completed", "attempt": 1})
    engine._authoritative_git_state_report = lambda: "GIT-SHOULD-NOT-APPEAR"
    prompt = (
        "Git durumunu tekrar etme. Yalnizca diskte kayitli mevcut "
        "self-development / own-code engineering cycle durumunu raporla. "
        "Stage, attempt, detail, changed paths, validation sonucu ve varsa "
        "recovery durumu ne? Hicbir kodu degistirme veya yeni islem baslatma."
    )
    result = engine._own_code_read_only_request(prompt)
    assert result is not None
    assert "KAYITLI ENGINEERING DURUMU" in result
    assert "Stage: completed" in result
    assert "GIT-SHOULD-NOT-APPEAR" not in result


def test_combined_engineering_and_git_request_reports_both():
    engine = _engine({"version": 4, "stage": "completed", "attempt": 1})
    engine._authoritative_git_state_report = lambda: "GERCEK GIT DURUMU"
    prompt = (
        "Kendi muhendislik durumunu incele. Devam eden veya yarim kalan "
        "self-development oturumu var mi? Hicbir kodu degistirme. "
        "Yalnizca mevcut kayitli durumu ve gercek Git durumunu raporla."
    )
    result = engine._own_code_read_only_request(prompt)
    assert "KAYITLI ENGINEERING DURUMU" in result
    assert "GERCEK GIT DURUMU" in result
