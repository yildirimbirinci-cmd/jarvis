from __future__ import annotations

import threading
from types import SimpleNamespace

from artmach_assistant.core.cancellable_ollama import OllamaChatResult
from artmach_assistant.core import local_dialogue as dialogue_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.local_dialogue import LocalDialogueManager


class _Lab:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int, bool]] = []

    def record(self, kind: str, elapsed: int, ok: bool) -> None:
        self.rows.append((kind, elapsed, ok))


def _manager() -> LocalDialogueManager:
    manager = LocalDialogueManager.__new__(LocalDialogueManager)
    manager.model = "local-chat"
    manager.url = "http://127.0.0.1:11434"
    manager.context_window = 4096
    manager.max_output_tokens = 512
    manager._history_lock = threading.RLock()
    manager.history = []
    manager.lab = _Lab()
    return manager


def test_stable_fact_detection_ignores_subjective_questions() -> None:
    assert LocalDialogueManager._looks_like_stable_factual_question(
        "Türkiye'nin başkenti neresidir"
    )
    assert LocalDialogueManager._looks_like_stable_factual_question(
        "Türkiye'nin başkenti İstanbul mudur"
    )
    assert not LocalDialogueManager._looks_like_stable_factual_question(
        "Sence İstanbul güzel mi?"
    )


def test_evidence_answer_is_context_free_and_uses_only_supplied_sources(monkeypatch) -> None:
    manager = _manager()
    captured = {}

    def fake_chat(_url, payload, **_kwargs):
        captured.update(payload)
        return OllamaChatResult(
            content="Hayır. Türkiye'nin başkenti Ankara'dır.",
            done_reason="stop",
            total_bytes=80,
            chunks=1,
        )

    monkeypatch.setattr(dialogue_module, "ollama_chat", fake_chat)
    answer = manager.answer_from_evidence(
        "Türkiye'nin başkenti İstanbul mudur",
        "[1] Resmi kaynak\nTürkiye'nin başkenti Ankara'dır.",
    )

    assert answer == "Hayır. Türkiye'nin başkenti Ankara'dır."
    assert len(captured["messages"]) == 2
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"
    assert all(row["role"] != "assistant" for row in captured["messages"])


class _Dialogue:
    def __init__(self) -> None:
        self.remembered: list[tuple[str, str]] = []
        self.ground_calls: list[tuple[str, str]] = []

    @staticmethod
    def _looks_like_stable_factual_question(text: str) -> bool:
        return "başkenti" in text.casefold()

    def answer_from_evidence(self, question: str, evidence: str, **_kwargs) -> str:
        self.ground_calls.append((question, evidence))
        return "Türkiye'nin başkenti Ankara'dır."

    def remember(self, user: str, assistant: str) -> None:
        self.remembered.append((user, assistant))


class _Researcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, max_results: int = 6):
        self.queries.append((query, max_results))
        if self.fail:
            raise RuntimeError("offline")
        return SimpleNamespace(
            source_text=lambda: "[1] Kaynak\nTürkiye'nin başkenti Ankara'dır."
        )


def _engine(*, internet: bool, fail: bool = False):
    dialogue = _Dialogue()
    researcher = _Researcher(fail=fail)
    return SimpleNamespace(
        dialogue=dialogue,
        researcher=researcher,
        config=SimpleNamespace(internet_research_enabled=internet),
        _interaction_cancelled=lambda: False,
        _interaction_model_progress=lambda _value: None,
    )


def test_general_dialogue_finalizer_does_not_silently_browse_with_permission() -> None:
    engine = _engine(internet=True)
    result = AssistantEngine._finalize_general_dialogue_answer(
        engine,
        "Türkiye'nin başkenti neresidir",
        "Türkiye'nin başkenti İstanbul'dur.",
    )
    assert "doğrulanmış kanıtım yok" in result
    assert "İstanbul" not in result
    assert engine.researcher.queries == []


def test_general_dialogue_finalizer_never_trusts_candidate_without_evidence() -> None:
    engine = _engine(internet=False)
    result = AssistantEngine._finalize_general_dialogue_answer(
        engine,
        "Türkiye'nin başkenti neresidir",
        "Türkiye'nin başkenti İstanbul'dur.",
    )
    assert "doğrulanmış kanıtım yok" in result
    assert "İstanbul" not in result
    assert engine.researcher.queries == []


def test_general_dialogue_finalizer_does_not_touch_researcher_for_plain_chat() -> None:
    engine = _engine(internet=True, fail=True)
    result = AssistantEngine._finalize_general_dialogue_answer(
        engine,
        "Türkiye'nin başkenti neresidir",
        "Türkiye'nin başkenti İstanbul'dur.",
    )
    assert "doğrulanmış kanıtım yok" in result
    assert "İstanbul" not in result
    assert engine.researcher.queries == []


def test_chat_intent_is_not_persisted_before_normal_responder_grounding() -> None:
    dialogue = _Dialogue()
    engine = SimpleNamespace(dialogue=dialogue, dialogue_active=False)
    decision = SimpleNamespace(
        kind="chat",
        response="Türkiye'nin başkenti İstanbul'dur.",
    )
    result = AssistantEngine._accept_dialogue_only_decision(
        engine,
        "Türkiye'nin başkenti neresidir",
        decision,
    )
    assert result is None
    assert dialogue.remembered == []
