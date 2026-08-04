from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine_with_failed_repair() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)

    session = SimpleNamespace(
        active=True,
        state="proposal_failed",
        plan_id="RPR-ABCDEF1234",
        finding_id="RUN-ABCDEF1234",
        approved_paths=("core/example.py",),
        approved_symbols=("Example.run",),
        attempts=1,
        last_error="unused private helper",
    )

    store = SimpleNamespace(
        load=lambda: session,
        invalidate_if_source_changed=lambda _fingerprint: session,
    )

    engine._self_repair_store = lambda: store
    engine._current_source_fingerprint = lambda: "source-fingerprint"
    engine._asks_for_latest_runtime_finding = lambda _text: False

    return engine


def test_failed_repair_apply_command_never_reaches_dialogue_model() -> None:
    engine = _engine_with_failed_repair()

    result = engine._reserved_self_repair_request(
        "taslagi onayla"
    )

    assert result is not None
    assert "uygulanabilir bekleyen taslak yok" in result
    assert "Hicbir dosya degistirilmedi" in result
    assert "uygulanmis gibi raporlamayacagim" in result


def test_failed_repair_start_command_keeps_existing_retry_guidance() -> None:
    engine = _engine_with_failed_repair()

    result = engine._reserved_self_repair_request("basla")

    assert result is not None
    assert "taslagi basarisiz oldu" in engine.command_key(result)
    assert "onarimi yeniden dene" in engine.command_key(result)
