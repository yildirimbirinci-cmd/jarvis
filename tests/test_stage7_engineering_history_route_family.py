from __future__ import annotations

from pathlib import Path
import re

from artmach_assistant.core.assistant import AssistantEngine


def test_all_engineering_history_route_calls_have_methods():
    source = Path(AssistantEngine.__module__.replace(".", "/") + ".py")
    if not source.is_file():
        source = Path("core/assistant.py")
    text = source.read_text(encoding="utf-8")
    calls = set(re.findall(r"self\.(\_[A-Za-z0-9_]*engineering_history_request)\s*\(", text))
    defs = set(re.findall(r"^    def (\_[A-Za-z0-9_]*engineering_history_request)\s*\(", text, re.MULTILINE))
    assert calls <= defs, f"missing route methods: {sorted(calls - defs)}"


def test_known_engineering_history_routes_are_safe_fallthrough():
    engine = object.__new__(AssistantEngine)
    for name in (
        "_rejected_engineering_history_request",
        "_accepted_engineering_history_request",
    ):
        method = getattr(engine, name, None)
        if method is not None:
            assert method("son tamamlanan engineering outcome dan ne ogrendin") is None


def test_stage7_persistent_learning_handler_still_exists():
    assert callable(getattr(AssistantEngine, "_persistent_engineering_learning_request", None))
