from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


class _EmptyRepairStore:
    def load(self):
        return None


def _engine():
    engine = object.__new__(AssistantEngine)
    engine.last_action_context = {}
    engine._asks_for_one_shot_maintenance = lambda text: False
    engine._self_repair_store = lambda: _EmptyRepairStore()
    engine._latest_runtime_finding = lambda: SimpleNamespace(
        finding_id="RUN-06578E9EDE"
    )
    engine.maintenance_review = lambda **kwargs: "MAINTENANCE"
    engine.prepare_runtime_improvement_implementation = (
        lambda finding_id: f"PREPARE:{finding_id}"
    )
    engine.run_autonomous_runtime_repair = (
        lambda finding_id: f"AUTONOMOUS:{finding_id}"
    )
    return engine


def test_live_natural_research_learn_and_do_routes_to_autonomous_repair():
    engine = _engine()
    result = engine._reserved_self_repair_request(
        "bu tekrarlanan yavaş işlemi nasıl düzelteceğini araştırıp öğren ve yap"
    )
    assert result == "AUTONOMOUS:RUN-06578E9EDE"


def test_runtime_problem_research_without_execute_only_prepares():
    engine = _engine()
    result = engine._reserved_self_repair_request(
        "bu tekrarlanan yavaş işlemi nasıl düzelteceğini araştır"
    )
    assert result == "PREPARE:RUN-06578E9EDE"


def test_generic_research_and_do_does_not_start_self_repair():
    engine = _engine()
    result = engine._reserved_self_repair_request(
        "istanbul tarihini araştır öğren ve yap"
    )
    assert result is None
