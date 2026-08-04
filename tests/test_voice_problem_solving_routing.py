from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.collaborative_problem_solving import (
    CollaborativeProblemSession,
    SolutionOption,
)


def voice_session() -> CollaborativeProblemSession:
    session = CollaborativeProblemSession.create(
        scope='C:/jarvis',
        problem='Seste problem var, incele ve sorunu gider.',
    )
    session.options = (
        SolutionOption(
            option_id='OPT-3',
            title='Önce kontrollü yeniden üretim',
            approach=(
                'Kod değiştirmeden aynı cümleyi birkaç kez çalıştırıp '
                'her ses aşamasını ölçmek ve hatayı tek aşamaya bağlamak.'
            ),
            risk='Düşük',
            expected_result='Yanlış ses bileşenini değiştirme riskini azaltmak.',
        ),
    )
    session.selected_option_id = 'OPT-3'
    session.touch(stage='awaiting_plan_approval')
    return session


class Store:
    def __init__(self, session):
        self.session = session

    def load(self):
        return self.session

    def save(self, session):
        self.session = session


def test_controlled_voice_option_is_classified_without_global_threshold_changes() -> None:
    session = voice_session()
    assert AssistantEngine._selected_option_is_controlled_voice_diagnostic(session)


def test_regular_code_solution_is_not_misclassified() -> None:
    session = voice_session()
    session.options = (
        SolutionOption(
            option_id='OPT-1',
            title='Ses zincirindeki gerçek gecikme noktasını düzelt',
            approach='core/voice_service.py içinde hedefli kod değişikliği yap.',
            risk='Orta',
            expected_result='TTS gecikmesini azaltmak.',
        ),
    )
    session.selected_option_id = 'OPT-1'
    assert not AssistantEngine._selected_option_is_controlled_voice_diagnostic(session)


def test_start_is_consumed_before_stale_own_code_plan() -> None:
    session = voice_session()
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = Store(session)
    engine._reserved_self_repair_request = lambda _text: None
    engine._own_code_read_only_request = lambda _text: None
    engine._start_voice_diagnostic_session = lambda: 'VDG-TEST tanılama başladı.'
    engine._handle_own_code_plan_follow_up = lambda _text: (_ for _ in ()).throw(
        AssertionError('Eski own-code planı bu komutu tüketmemeliydi.')
    )

    answer = engine.handle_local_command('Başla')

    assert answer == 'VDG-TEST tanılama başladı.'
    assert engine.collaborative_problems.session.stage == 'diagnostic_running'


def test_direct_collaborative_approval_starts_diagnostic_not_patch() -> None:
    session = voice_session()
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = Store(session)
    engine.last_action_context = None
    engine._start_voice_diagnostic_session = lambda: 'VDG-DIRECT tanılama başladı.'
    engine.prepare_own_code_proposal = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('Kod taslağı hazırlanmamalıydı.')
    )

    answer = engine._collaborative_problem_request('Başla')

    assert answer == 'VDG-DIRECT tanılama başladı.'
    assert engine.collaborative_problems.session.stage == 'diagnostic_running'
