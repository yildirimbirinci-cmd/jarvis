from pathlib import Path


def test_own_code_plan_approval_precedes_generic_tool_approval() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "core" / "assistant.py"
    ).read_text(encoding="utf-8")

    plan_follow_up = source.index(
        "plan_follow_up = self._handle_own_code_plan_follow_up(text)"
    )
    tool_follow_up = source.index(
        "tool_follow_up = self.agent_tool_commands.handle(text)"
    )

    assert plan_follow_up < tool_follow_up
