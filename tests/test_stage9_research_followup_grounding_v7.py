from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


class _Config:
    internet_research_enabled = True
    def save(self):
        return None


class _Dialogue:
    def __init__(self):
        self.requested = []
    def latest_user_message(self, *, exclude=""):
        self.requested.append(exclude)
        return "istanbul bir başkentmidir"


class _Result:
    def __init__(self, query):
        self.query = query
    def report(self):
        return f"ARAŞTIRMA: {self.query}"


def _engine():
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = _Config()
    engine.dialogue = _Dialogue()
    engine.pending_research_query = ""
    engine.pending_research_mode = ""
    engine.pending_research_own_code = False
    engine.research = lambda query: _Result(query)
    return engine


def test_longest_research_marker_does_not_leave_stir_fragment():
    engine = _engine()
    result = engine._internet_research_request("istanbul internette araştır")
    assert result == "ARAŞTIRMA: istanbul"
    assert "stir" not in result


def test_deictic_research_followup_resolves_previous_user_turn():
    engine = _engine()
    result = engine._internet_research_request("bunu internette araştır ve doğrula")
    assert result == "ARAŞTIRMA: istanbul bir başkentmidir"
    assert engine.dialogue.requested


def test_unrelated_youtube_result_is_not_accepted_as_evidence():
    source = ResearchSource(
        title="YouTube",
        url="https://www.youtube.com/",
        snippet="Enjoy the videos and music you love.",
        content="Watch and share videos online.",
    )
    assert not ResearchManager._source_relevant_to_query(
        "istanbul bir başkentmidir", source
    )


def test_relevant_istanbul_source_is_accepted():
    source = ResearchSource(
        title="Istanbul",
        url="https://example.com/istanbul",
        snippet="Istanbul is the largest city in Turkey.",
        content="Ankara is the capital of Turkey; Istanbul is not the national capital.",
    )
    assert ResearchManager._source_relevant_to_query(
        "istanbul bir başkentmidir", source
    )


def test_normal_factual_finalizer_does_not_silently_browse():
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.dialogue = type("D", (), {"_looks_like_stable_factual_question": staticmethod(lambda _t: True)})()
    engine.command_key = lambda text: text.casefold().replace("ş", "s").replace("ı", "i")
    engine.researcher = type("R", (), {"search": lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not browse"))})()
    answer = engine._finalize_general_dialogue_answer(
        "türkiyenin başkenti neresidir", "Türkiye'nin başkenti İstanbul'dur."
    )
    assert "uydurmayacağım" in answer
    assert "İstanbul'dur" not in answer


def test_v7_sources_keep_explicit_research_contract():
    source = Path(AssistantEngine.__module__.replace('.', '/'))
    # Runtime behavior tests above are authoritative; this marker protects the
    # user-facing permission contract from accidental silent-browse regressions.
    text = Path(__import__('artmach_assistant.core.assistant', fromlist=['x']).__file__).read_text(encoding='utf-8')
    assert "Yalnızca açıkça" in text
