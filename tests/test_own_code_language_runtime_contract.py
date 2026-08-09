from __future__ import annotations

from pathlib import Path

import artmach_assistant.core.assistant as assistant_module


def test_runtime_learning_precedes_structured_execution() -> None:
    source = Path(assistant_module.__file__).read_text(encoding="utf-8")
    learning = source.index("language_learning = self._own_code_language_learning_request(text)")
    structured = source.index("structured_own_code = self._structured_own_code_command_request(text)")
    assert learning < structured


def test_structured_router_uses_persistent_user_language_store() -> None:
    source = Path(assistant_module.__file__).read_text(encoding="utf-8")
    start = source.index("def _structured_own_code_command_request")
    end = source.index("def _engineering_state_report", start)
    block = source[start:end]
    assert "learned_store_path=OWN_CODE_USER_LANGUAGE_FILE" in block
