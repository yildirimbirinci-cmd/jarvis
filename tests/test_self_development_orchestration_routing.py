from artmach_assistant.core.assistant import AssistantEngine


class _Store:
    def load(self):
        raise AssertionError("Explicit own-code plan must bypass collaborative store")


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.last_action_context = None
    engine.collaborative_problems = _Store()
    return engine


def test_explicit_own_code_plan_bypasses_collaborative_problem_flow() -> None:
    engine = _engine()
    result = engine._collaborative_problem_request(
        "Kendi kodunu geliştir. .jarvis_fix_backup klasörünü üretim kaynak "
        "taramasından tamamen hariç tut. Önce teknik plan hazırla, hiçbir "
        "dosyayı henüz değiştirme."
    )
    assert result is None


def test_plain_problem_statement_still_uses_collaborative_flow() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.last_action_context = None
    engine.collaborative_problems = None
    assert engine._collaborative_problem_request(
        "Jarvis bazen cevap verirken donuyor, sebebini birlikte inceleyelim."
    ) is None
