from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.learning_memory import LearningMemory, LearnedMemory
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource


class _Config:
    internet_research_enabled = True

    def save(self) -> None:
        return None


class _Memory:
    def __init__(self) -> None:
        self.taught = []
        self.audits = []

    def teach(self, kind, trigger, **kwargs):
        self.taught.append((kind, trigger, kwargs))
        return SimpleNamespace(kind=kind, trigger=trigger, **kwargs)

    def audit(self, event, **details):
        self.audits.append((event, details))


class _Dialogue:
    def __init__(self) -> None:
        self.latest = "istanbul bir başkentmidir"
        self.evidence_calls = []

    def latest_user_message(self, *, exclude=""):
        return self.latest

    def answer_from_evidence(self, question, evidence, **_kwargs):
        self.evidence_calls.append((question, evidence))
        if "istanbul" in question.casefold():
            return "Hayır. İstanbul Türkiye'nin başkenti değildir; Türkiye'nin başkenti Ankara'dır."
        return "Türkiye'nin başkenti Ankara'dır."


class _Researcher:
    def search(self, query, max_results=6):
        return ResearchResult(
            query=query,
            sources=[
                ResearchSource(
                    title="Turkey (Türkiye)",
                    url="https://example.com/turkiye",
                    snippet="Ankara is the capital city of Turkey.",
                    content="Istanbul is not the capital of Turkey. Ankara is the capital city, while Istanbul is the largest city.",
                )
            ],
        )


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = _Config()
    engine.dialogue = _Dialogue()
    engine.researcher = _Researcher()
    engine.learning_memory = _Memory()
    engine.pending_research_query = ""
    engine.pending_research_mode = ""
    engine.pending_research_own_code = False
    engine.pending_research_learn = False
    engine.dialogue_active = False
    engine._interaction_cancelled = lambda: False
    engine._interaction_model_progress = lambda _value: None
    return engine


def test_research_answers_the_question_from_evidence_instead_of_generic_summary() -> None:
    engine = _engine()
    result = engine.research("turkiye nin baskenti")
    assert result.summary == "Türkiye'nin başkenti Ankara'dır."
    assert engine.dialogue.evidence_calls
    question, evidence = engine.dialogue.evidence_calls[-1]
    assert question == "turkiye nin baskenti"
    assert "Ankara is the capital city" in evidence


def test_explicit_research_and_learn_strips_learning_words_from_query() -> None:
    engine = _engine()
    result = engine._internet_research_request(
        "türkiye'nin başkentini internette araştır ve öğren"
    )
    assert "ARAŞTIRMA: turkiye nin baskentini" in result
    assert "ve ogren" not in result
    assert "kalıcı yerel bilgi hafızama kaydettim" in result
    assert len(engine.learning_memory.taught) == 1
    kind, trigger, kwargs = engine.learning_memory.taught[0]
    assert kind == "verified_fact"
    assert trigger == "turkiye nin baskentini"
    assert kwargs["response"] == "Türkiye'nin başkenti Ankara'dır."
    assert kwargs["source"] == "verified_internet_research_v2"
    assert "Ankara is the capital city" in kwargs["evidence"]
    assert kwargs["evidence_url"] == "https://example.com/turkiye"


def test_deictic_research_and_learn_reuses_previous_user_question() -> None:
    engine = _engine()
    result = engine._internet_research_request("bunu internette araştır ve öğren")
    assert "ARAŞTIRMA: istanbul bir başkentmidir" in result
    assert engine.learning_memory.taught[0][1] == "istanbul bir başkentmidir"


def test_unverified_research_answer_is_not_learned() -> None:
    engine = _engine()
    result = ResearchResult(
        query="x",
        sources=[ResearchSource("x", "https://example.com", "x", "x")],
        summary="Kaynaklar bu soruyu güvenilir biçimde doğrulamıyor.",
    )
    assert engine._learn_verified_research_fact("x", result) is False
    assert engine.learning_memory.taught == []


def test_verified_fact_is_returned_without_running_an_action() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    record = LearnedMemory(
        kind="verified_fact",
        trigger="turkiye nin baskentini",
        response="Türkiye'nin başkenti Ankara'dır.",
        source="verified_internet_research",
    )
    assert engine._execute_learned_memory(record) == "Türkiye'nin başkenti Ankara'dır."


def test_verified_fact_survives_restart_and_minor_typo(tmp_path) -> None:
    path = tmp_path / "learned_memory.json"
    first = LearningMemory(path)
    first.teach(
        "verified_fact",
        "turkiye nin baskentini",
        response="Türkiye'nin başkenti Ankara'dır.",
        source="verified_internet_research_v2",
        confidence=1.0,
        evidence="Ankara is the capital city of Turkey.",
        evidence_url="https://example.com/turkiye",
    )

    restarted = LearningMemory(path)
    matched = restarted.match("Türkiye'nin başkebnti neresi")
    assert matched is not None
    assert matched.kind == "verified_fact"
    assert matched.response == "Türkiye'nin başkenti Ankara'dır."


def test_verified_fact_typo_tolerance_does_not_match_different_subject(tmp_path) -> None:
    path = tmp_path / "learned_memory.json"
    memory = LearningMemory(path)
    memory.teach(
        "verified_fact",
        "turkiye nin baskentini",
        response="Türkiye'nin başkenti Ankara'dır.",
        source="verified_internet_research_v2",
        confidence=1.0,
        evidence="Ankara is the capital city of Turkey.",
        evidence_url="https://example.com/turkiye",
    )
    assert memory.match("İstanbul bir başkent midir") is None


def test_deictic_research_learn_and_tell_reuses_previous_question() -> None:
    engine = _engine()
    result = engine._internet_research_request(
        "bu konuyu internette araştır, öğren ve bana anlat"
    )
    assert "ARAŞTIRMA: istanbul bir başkentmidir" in result
    assert "ogren ve bana anlat" not in result.casefold()
    assert engine.learning_memory.taught[0][1] == "istanbul bir başkentmidir"


def test_deictic_research_tell_directive_does_not_pollute_query() -> None:
    engine = _engine()
    result = engine._internet_research_request(
        "bu konuyu internette araştır ve bana anlat"
    )
    assert "ARAŞTIRMA: istanbul bir başkentmidir" in result
    assert "bana anlat" not in result.casefold()
