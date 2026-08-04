from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.collaborative_problem_solving import (
    CollaborativeProblemSession,
    SolutionOption,
)


PHRASE = (
    "Bu onaylanan planı uygulanabilir bir kod değişikliği taslağına dönüştür. "
    "Henüz kodu değiştirme."
)


class Store:
    def __init__(self, session):
        self.session = session

    def load(self):
        return self.session

    def save(self, session):
        self.session = session


def active_session() -> CollaborativeProblemSession:
    session = CollaborativeProblemSession.create(
        scope="C:/jarvis",
        problem="Genel bir çalışma zamanı problemini çöz.",
    )
    session.options = (
        SolutionOption(
            option_id="OPT-1",
            title="Kök nedene hedefli düzeltme",
            approach="Doğrulanmış kanıta göre en küçük kod değişikliğini hazırla.",
            risk="Düşük",
            expected_result="Sorunu en az yan etkiyle gidermek.",
            validation=("Odaklı testleri çalıştır.",),
            affected_paths=("core/assistant.py",),
        ),
    )
    session.selected_option_id = "OPT-1"
    session.plan_instruction = "Kanıta bağlı en küçük düzeltme taslağını hazırla."
    session.candidate_paths = ("core/assistant.py",)
    session.touch(stage="awaiting_plan_approval")
    return session


def test_plan_to_draft_language_is_not_misread_as_apply() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)

    assert engine._own_code_approval_request(PHRASE) is None


def test_plan_to_draft_language_is_collaborative_approval() -> None:
    assert AssistantEngine._collaborative_is_approval(PHRASE)


def test_active_problem_session_turns_approved_plan_into_proposal() -> None:
    session = active_session()
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = Store(session)
    engine.last_action_context = None
    engine.editor = SimpleNamespace(pending=None)
    engine._voice_diagnostic_request = lambda _text: None
    engine._collaborative_session_is_own = lambda _session: True

    def prepare(*_args, **_kwargs):
        engine.editor.pending = object()
        return "GENEL PROBLEM TASLAĞI HAZIR"

    engine.prepare_own_code_proposal = prepare

    answer = engine._collaborative_problem_request(PHRASE)

    assert "GENEL PROBLEM TASLAĞI HAZIR" in answer
    assert engine.collaborative_problems.session.stage == "proposal_ready"
