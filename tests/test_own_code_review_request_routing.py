from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.workspace import WorkspaceError


def _routing_engine() -> AssistantEngine:
    engine = object.__new__(AssistantEngine)
    engine.last_action_context = None
    return engine


def test_review_findings_request_does_not_generate_patch() -> None:
    engine = _routing_engine()

    result = engine._own_code_change_request(
        "Kodlarını incele ve geliştirilmesi gereken yerleri bana söyle."
    )

    assert result is None


def test_explicit_improvement_request_still_generates_proposal(monkeypatch) -> None:
    engine = _routing_engine()
    monkeypatch.setattr(
        engine,
        "prepare_own_code_plan",
        lambda text: f"plan:{text}",
    )

    result = engine._own_code_change_request(
        "Kendi kodlarını geliştir ve yanıt süresini hızlandır."
    )

    assert result is not None
    assert result.startswith("plan:")


def test_change_capability_question_does_not_generate_proposal() -> None:
    engine = _routing_engine()

    result = engine._own_code_change_request(
        "Kendi kodlarını değiştirebiliyor musun?"
    )

    assert result is None


def test_question_about_existing_code_findings_is_read_only(monkeypatch) -> None:
    engine = _routing_engine()
    monkeypatch.setattr(engine, "own_code_review_report", lambda: "inceleme raporu")
    question = "Kodların arasında düzeltilecek bir şey var mı?"

    assert engine._own_code_change_request(question) is None
    assert engine._own_code_request(question) == "inceleme raporu"


def test_review_summary_wording_does_not_generate_change_plan(monkeypatch) -> None:
    engine = _routing_engine()
    monkeypatch.setattr(engine, "own_code_review_report", lambda: "inceleme raporu")
    question = (
        "Kendi kodlarını inceleyip değiştirilmesi gereken nereler var "
        "bana özetler misin?"
    )

    assert engine._own_code_change_request(question) is None
    assert engine._own_code_request(question) == "inceleme raporu"


def test_corrupted_code_capability_wording_is_answered_deterministically() -> None:
    engine = _routing_engine()

    result = engine._own_code_request("Kodlarını güzellebiliyorsun.")

    assert result is not None
    assert "taslağı hazırlayabilir" in result


def test_incomplete_sentence_is_not_saved_as_language_correction() -> None:
    engine = _routing_engine()

    assert engine._apply_direct_language_correction("Bu bir soru değil, bir...") is None


def test_generic_program_capability_is_answered_locally() -> None:
    engine = _routing_engine()

    result = engine._fast_capability_question(
        "Programları açabiliyor musun?"
    )

    assert result is not None
    assert "Kayıtlı uygulamaları" in result


def test_invalid_model_patch_is_repaired_once(
    tmp_path: Path, monkeypatch
) -> None:
    engine = _routing_engine()
    engine.config = SimpleNamespace(
        model="local-model", ollama_url="http://127.0.0.1:11434"
    )
    engine.workspace = SimpleNamespace(
        set_workspace=lambda _root: None,
        invalidate_index=lambda: None,
        call_graph_patch_context=lambda *_args, **_kwargs: SimpleNamespace(
            text="DOSYA: core/example.py\nprint('ok')\n",
            used_call_graph=True,
        ),
    )
    calls = []
    proposal = SimpleNamespace(
        summary="Onarıldı",
        files=[SimpleNamespace(path="core/example.py")],
    )

    def create_proposal(raw: str):
        calls.append(raw)
        if len(calls) == 1:
            raise WorkspaceError(
                "Patch doğrulaması başarısız: python_syntax"
            )
        return proposal

    engine.editor = SimpleNamespace(create_proposal=create_proposal)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "own_project_root", lambda: tmp_path)

    responses = iter(
        (
            {"message": {"content": '{"files":[{"content":"broken"}]}'}},
            {
                "message": {
                    "content": (
                        '{"summary":"Onarıldı","files":'
                        '[{"path":"core/example.py","reason":"test",'
                        '"content":"print(\\"ok\\")\\\\n"}]}'
                    )
                }
            },
        )
    )

    class _Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    monkeypatch.setattr(
        "artmach_assistant.core.assistant.urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(next(responses)),
    )

    result = engine.prepare_own_code_proposal(
        "Kendi kodundaki örneği düzelt."
    )

    assert len(calls) == 2
    assert "Kod değişikliği önerisini hazırladım" in result


def test_code_review_follow_up_uses_previous_local_report() -> None:
    engine = _routing_engine()
    engine.last_action_context = {
        "kind": "own_code_review",
        "target": "Kendi kaynak kodu incelemesi",
        "detail": (
            "KOD İNCELEME ÖZETİ\n"
            "STYLE: 12 | SECURITY: 1 | COMPLEXITY: 2\n\n"
            "[STYLE] ui.py:10 — 140 karakterden uzun satır\n"
            "[COMPLEXITY] core/a.py:20 — run: 80 satırdan uzun fonksiyon\n"
            "[SECURITY] core/b.py:5 — Dinamik kod çalıştırma kullanımı\n"
        ),
    }

    result = engine._handle_action_follow_up(
        "Geliştirilmesi gereken yerler neler?"
    )

    assert result is not None
    assert "core/b.py:5" in result
    assert result.index("core/b.py:5") < result.index("core/a.py:20")
    assert "kodu inceleyemem" not in result.casefold()


def test_initial_code_review_answer_contains_actionable_locations(
    monkeypatch,
) -> None:
    engine = _routing_engine()
    report = (
        "KOD İNCELEME ÖZETİ\n"
        "STYLE: 12 | SECURITY: 1 | COMPLEXITY: 2\n\n"
        "[STYLE] ui.py:10 — 140 karakterden uzun satır\n"
        "[SECURITY] core/b.py:5 — Dinamik kod çalıştırma kullanımı\n"
    )

    class _Reviewer:
        def __init__(self, _workspace):
            pass

        def report(self):
            return report

    method_globals = AssistantEngine.own_code_review_report.__globals__
    monkeypatch.setitem(
        method_globals, "WorkspaceService", lambda _root: object()
    )
    monkeypatch.setitem(method_globals, "CodeReviewService", _Reviewer)

    result = engine.own_code_review_report()

    assert "core/b.py:5" in result
    assert "Geliştirme önceliği" in result
    assert engine.last_action_context["kind"] == "own_code_review"
