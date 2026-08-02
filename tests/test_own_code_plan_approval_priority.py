from pathlib import Path

def test_own_code_plan_follow_up_precedes_collaborative_problem() -> None:
    package_root = Path(__file__).resolve().parents[1]
    source = (package_root / "core" / "assistant.py").read_text(encoding="utf-8")
    plan_position = source.index(
        "plan_follow_up = self._handle_own_code_plan_follow_up(text)"
    )
    collaborative_position = source.index(
        "collaborative_problem = self._collaborative_problem_request(text)"
    )
    assert plan_position < collaborative_position

def test_short_plan_approval_has_deterministic_fallback() -> None:
    package_root = Path(__file__).resolve().parents[1]
    source = (package_root / "core" / "assistant.py").read_text(encoding="utf-8")
    assert '"planı onayla"' in source
    assert '"plani onayla"' in source


def test_own_code_apply_precedes_collaborative_problem() -> None:
    package_root = Path(__file__).resolve().parents[1]
    source = (package_root / "core" / "assistant.py").read_text(encoding="utf-8")
    approval_position = source.index(
        "own_code_approval = self._own_code_approval_request(text)"
    )
    collaborative_position = source.index(
        "collaborative_problem = self._collaborative_problem_request(text)"
    )
    assert approval_position < collaborative_position
