from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


class _Config:
    model = "legacy-coder"
    chat_model = "fast-chat"
    code_model = "code-model"
    ollama_url = "http://127.0.0.1:11434"
    internet_research_enabled = False

    def __init__(self) -> None:
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = _Config()
    engine.pending_research_query = ""
    engine.dialogue_active = False
    engine.dialogue = SimpleNamespace(
        lab=SimpleNamespace(report=lambda: "laboratuvar"),
    )
    return engine


def test_plain_latency_request_is_not_hijacked_by_model_lab() -> None:
    engine = _engine()

    assert engine._model_lab_request(
        "Konuşmalarımızdaki gecikmeyi azaltmanı istiyorum"
    ) is None
    assert engine._model_lab_request("Model laboratuvarı gecikme raporunu göster") == (
        "laboratuvar"
    )


def test_model_names_are_reported_from_configuration_not_invented() -> None:
    engine = _engine()
    answer = engine._local_model_request(
        "Konuşma modeli ve kod modelinin adını söyle"
    )
    assert answer == "Konuşma modelim: fast-chat. Kod modelim: code-model."


def test_internet_capability_question_is_answered_without_using_network() -> None:
    engine = _engine()
    answer = engine._internet_research_request(
        "İnternette araştırma yapabiliyor musun"
    )
    assert "Evet" in answer
    assert "şu anda kapalı" in answer
    assert engine.pending_research_query == ""


def test_research_requires_explicit_permission_and_resumes_pending_query() -> None:
    engine = _engine()
    engine.research = lambda query: SimpleNamespace(
        report=lambda: f"ARAŞTIRMA TAMAMLANDI: {query}"
    )

    first = engine._internet_research_request(
        "İnternette ara Python 3.11 güncel güvenlik durumu"
    )

    assert "izin" in first.casefold()
    assert engine.pending_research_query == "python 3.11 guncel guvenlik durumu"
    assert engine.config.internet_research_enabled is False

    second = engine._internet_research_request(
        "internet araştırmasına izin ver"
    )

    assert second == (
        "ARAŞTIRMA TAMAMLANDI: python 3.11 guncel guvenlik durumu"
    )
    assert engine.config.internet_research_enabled is True
    assert engine.config.saved == 1
    assert engine.pending_research_query == ""


def test_research_permission_can_be_revoked() -> None:
    engine = _engine()
    engine.config.internet_research_enabled = True

    answer = engine._internet_research_request("internet iznini kaldır")

    assert answer == "İnternet araştırması iznini kapattım."
    assert engine.config.internet_research_enabled is False
    assert engine.config.saved == 1


def test_targeted_own_source_search_is_not_mistaken_for_version_history() -> None:
    engine = _engine()
    engine.last_action_context = None
    engine.own_project_root = lambda: "/project/artmach_assistant"
    engine.workspace = SimpleNamespace(
        set_workspace=lambda _root: None,
        call_graph_patch_context=lambda *_args, **_kwargs: SimpleNamespace(
            text=(
                "DOSYA: core/local_dialogue.py | LocalDialogueManager\n"
                "DOSYA: core/assistant.py | AssistantEngine\n"
            )
        ),
    )
    text = "Kendi kodların arasında sohbet etme özelliğini çalıştıran kodları bul"
    assert engine._own_code_version_request(text) is None
    answer = engine._own_code_request(text)
    assert "core/local_dialogue.py" in answer
    assert "core/assistant.py" in answer
    assert "dosyalar değiştirilmedi" in answer
