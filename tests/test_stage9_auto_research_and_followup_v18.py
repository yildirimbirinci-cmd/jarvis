from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.fact_research_runtime import FactResearchRuntime
from artmach_assistant.core.research_manager import ResearchManager, ResearchResult, ResearchSource


class _Config:
    internet_research_enabled = True


class _Dialogue:
    def __init__(self):
        self.rows = []

    def remember(self, user_text, answer):
        self.rows.append((user_text, answer))


class _Engine:
    def __init__(self, result):
        self.config = _Config()
        self.dialogue = _Dialogue()
        self.fact_research = FactResearchRuntime()
        self._result = result
        self.queries = []

    def research(self, query):
        self.queries.append(query)
        return self._result


def _result(*, learnable=True, summary="Turgut Ozal Turkiye'nin 8. Cumhurbaskanidir."):
    return ResearchResult(
        query="turgut ozal kimdir",
        sources=[ResearchSource("Turgut Ozal", "https://www.tccb.gov.tr/example", summary)],
        summary=summary,
        evidence_learnable=learnable,
    )


def test_unknown_world_fact_auto_researches_when_internet_permission_is_enabled():
    engine = _Engine(_result())
    answer = AssistantEngine._auto_research_world_fact(engine, "turgut ozal kimdir", "turgut ozal kimdir")
    assert engine.queries == ["turgut ozal kimdir"]
    assert answer is not None
    assert "ARAŞTIRMA: turgut ozal kimdir" in answer
    assert engine.fact_research.session.query == "turgut ozal kimdir"


def test_auto_research_refuses_unverified_result():
    engine = _Engine(_result(learnable=False, summary="Kaynaklar yetersiz."))
    answer = AssistantEngine._auto_research_world_fact(engine, "bilinmeyen soru", "bilinmeyen soru")
    assert answer is None


def test_contextual_date_followup_reuses_previous_research_query():
    runtime = FactResearchRuntime()
    runtime.begin_research("fenerbahcenin son maci kimleydi?")
    resolved = runtime.resolve_factual_question("bu tarihte kim ile mac yapti ve skor nedir")
    assert resolved == "fenerbahcenin son maci kimleydi? kim ile mac yapti ve skor nedir"


def test_opponent_and_score_questions_require_complete_answer():
    query = "fenerbahcenin son maci kimleydi ve skor nedir"
    assert not ResearchManager.answer_satisfies_question(
        query, "Fenerbahcenin son maci 10 Agustos 2026 tarihinde oynandi."
    )
    assert ResearchManager.answer_satisfies_question(
        query, "Fenerbahce Feyenoord ile oynadi ve maci 5-2 kazandi."
    )


def test_grounded_answer_removes_unsupported_atomic_claim():
    sources = [ResearchSource(
        "Turgut Ozal",
        "https://www.tccb.gov.tr/cumhurbaskanlarimiz/turgut_ozal/",
        "Turgut Ozal 1927 yilinda Malatya'da dogdu ve Turkiye Cumhuriyeti'nin 8. Cumhurbaskani oldu.",
        "Turgut Ozal 1927 yilinda Malatya'da dogdu. 1989 yilinda Cumhurbaskani secildi.",
    )]
    candidate = (
        "Turgut Ozal 1927 yilinda Malatya'da dogdu. "
        "Ozal Milliyetci Parti ile ilgili faaliyetlerde bulundu."
    )
    grounded = ResearchManager.grounded_answer("Turgut Ozal kimdir", candidate, sources)
    assert "1927" in grounded
    assert "Milliyetci Parti" not in grounded
