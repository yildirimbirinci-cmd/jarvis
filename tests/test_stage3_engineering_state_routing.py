from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.normalize_address = lambda value: value
    engine.command_key = lambda value: str(value).lower()
    engine._load_own_code_cycle = lambda: {
        "stage": "stale",
        "attempt": 0,
        "detail": "old terminal cycle",
        "changed_paths": [],
        "validation_summary": "",
    }
    engine._cycle_attempt = lambda cycle: int(cycle.get("attempt", 0))
    terminal = SimpleNamespace(
        terminal=True,
        session_id="PS-1955E25E8EF2",
        status="FAILED",
    )
    engine._evidence_patch_session_store = lambda: SimpleNamespace(
        load=lambda: terminal
    )
    return engine


def test_engineering_state_read_only_outranks_patch_session_router() -> None:
    engine = _engine()
    engine._patch_session_command_request = lambda text: (
        "WRONG PATCH SESSION ROUTE"
    )
    result = engine.handle_local_command(
        "PS-1955E25E8EF2 patch session dahil engineering state durumunu "
        "incele. Hicbir kodu degistirme. Yalnizca mevcut kayitli durumu raporla."
    )
    assert result.startswith("KAYITLI ENGINEERING DURUMU")
    assert "WRONG PATCH SESSION ROUTE" not in result
    assert "Patch session: TERMINAL" in result
    assert "Patch session status: FAILED" in result


def test_failed_patch_session_is_terminal_in_persisted_report() -> None:
    engine = _engine()
    result = engine._persisted_engineering_state_report()
    assert "Patch session: TERMINAL" in result
    assert "Patch session id: PS-1955E25E8EF2" in result
    assert "Patch session status: FAILED" in result

def test_exact_stage3_runtime_prompt_matches_read_only() -> None:
    text = (
        "Bu bir salt-okunur Aşama 3 durum denetimidir. "
        "PS-1955E25E8EF2 dahil hiçbir kaydı değiştirme veya temizleme. "
        "Mevcut persistent engineering kayıtlarını dosya/kayıt türüne göre "
        "tara ve yalnızca envanter çıkar: research session, patch session, "
        "pending proposal, approval state, validation/worktree state, apply "
        "state, retest state, rollback state, closeout state ve recovery state. "
        "Her kategori için kaç kayıt bulunduğunu, aktif olanların kimliğini ve "
        "terminal durumda olanların durumunu bildir. FAILED veya COMPLETED "
        "kayıtları devam ettirilebilir olarak sayma. Yeni araştırma, proposal, "
        "patch veya recovery başlatma."
    )
    assert AssistantEngine._asks_for_engineering_state_only(text) is True

def test_engineering_state_matcher_is_static_v6() -> None:
    import inspect
    raw = inspect.getattr_static(
        AssistantEngine,
        "_asks_for_engineering_state_only",
    )
    assert isinstance(raw, staticmethod)


def test_exact_runtime_prompt_matches_engineering_state_v6() -> None:
    text = (
        "Bu bir salt-okunur Aşama 3 durum denetimidir. "
        "PS-1955E25E8EF2 dahil hiçbir kaydı değiştirme veya temizleme. "
        "Mevcut persistent engineering kayıtlarını dosya/kayıt türüne göre "
        "tara ve yalnızca envanter çıkar: research session, patch session, "
        "pending proposal, approval state, validation/worktree state, apply "
        "state, retest state, rollback state, closeout state ve recovery state. "
        "Her kategori için kaç kayıt bulunduğunu, aktif olanların kimliğini ve "
        "terminal durumda olanların durumunu bildir. FAILED veya COMPLETED "
        "kayıtları devam ettirilebilir olarak sayma. Yeni araştırma, proposal, "
        "patch veya recovery başlatma."
    )
    assert AssistantEngine._asks_for_engineering_state_only(text) is True
