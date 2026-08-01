from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "apply_voice_gui_integration.py"
SPEC = importlib.util.spec_from_file_location("apply_voice_gui_integration_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
patcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = patcher
SPEC.loader.exec_module(patcher)


_REQUIRED = sorted(patcher.REQUIRED_METHODS)


def _source(*, omit: str = "") -> str:
    methods = []
    for name in _REQUIRED:
        if name == omit:
            continue
        methods.append(f"    def {name}(self, *args, **kwargs):\n        return None\n")
    return (
        "from pathlib import Path\n"
        "import sys\n\n"
        "class MainWindow:\n"
        "    def __init__(self):\n"
        "        self.value = 1\n\n"
        + "\n".join(methods)
        + "\ndef main():\n"
        "    return MainWindow()\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _project(tmp_path: Path, *, omit: str = "") -> tuple[Path, str]:
    root = tmp_path / "artmach_assistant"
    (root / "core").mkdir(parents=True)
    original = _source(omit=omit)
    (root / "app.py").write_text(original, encoding="utf-8")
    (root / "core" / "gui_voice_integration.py").write_text("# integration\n", encoding="utf-8")
    (root / "core" / "voice_turn_coordinator.py").write_text("# coordinator\n", encoding="utf-8")
    (root / "core" / "assistant.py").write_text(
        "class AssistantEngine:\n"
        "    def handle(self, raw_text, *, turn_id=None): pass\n"
        "    def response_packet(self, visible, *, turn_id=None): pass\n",
        encoding="utf-8",
    )
    (root / "core" / "conversation_runtime.py").write_text(
        "class ConversationRuntime:\n"
        "    def token_for(self, turn_id=None): pass\n"
        "    def is_current(self, turn_id): pass\n"
        "    def raise_if_cancelled(self, turn_id=None): pass\n"
        "    def begin_task(self, name, *, turn_id=None, cancellation=None): pass\n"
        "    def response_ready(self, visible, spoken, *, turn_id=None): pass\n"
        "    def packet_for(self, visible, renderer, *, turn_id=None): pass\n"
        "    def mark_speaking(self, *, turn_id=None, cancel_callback=None): pass\n"
        "    def complete(self, detail='', *, turn_id=None): pass\n"
        "    def cancel(self, detail='', *, turn_id=None): pass\n",
        encoding="utf-8",
    )
    (root / "core" / "task_orchestrator.py").write_text(
        "class TaskOrchestrator:\n"
        "    def start(self, name, source='ui', *, parent_token=None, turn_id=''): pass\n",
        encoding="utf-8",
    )
    (root / "core" / "voice_service.py").write_text(
        "class VoiceService:\n"
        "    def begin_speech_session(self): pass\n"
        "    def stop_speaking(self, session_id=None): pass\n"
        "    def speak(self, text, *, speech_session_id=None, cancel_check=None): pass\n",
        encoding="utf-8",
    )
    return root, original


def test_apply_is_atomic_idempotent_and_revert_restores_exact_source(tmp_path) -> None:
    root, original = _project(tmp_path)

    before = patcher.inspect(root)
    assert before.compatible is True
    assert before.installed is False

    after = patcher.apply(root)
    patched = (root / "app.py").read_text(encoding="utf-8")
    assert after.installed is True
    assert patched.count(patcher.IMPORT_LINE) == 1
    assert patched.count(patcher.INSTALL_LINE) == 1
    ast.parse(patched)
    backup = root / ".jarvis_backups" / "app.py.before_voice_gui_integration"
    assert backup.read_text(encoding="utf-8") == original

    patcher.apply(root)
    assert (root / "app.py").read_text(encoding="utf-8") == patched

    restored = patcher.revert(root)
    assert restored == root / "app.py"
    assert restored.read_text(encoding="utf-8") == original


def test_nested_project_root_is_supported(tmp_path) -> None:
    root, _original = _project(tmp_path)

    result = patcher.apply(tmp_path)

    assert result.app_path == root / "app.py"
    assert result.installed is True


def test_missing_expected_gui_method_fails_closed_without_writing(tmp_path) -> None:
    root, original = _project(tmp_path, omit="on_answer")

    inspection = patcher.inspect(root)
    assert inspection.compatible is False
    assert inspection.missing_methods == ("on_answer",)
    with pytest.raises(RuntimeError, match="on_answer"):
        patcher.apply(root)

    assert (root / "app.py").read_text(encoding="utf-8") == original
    assert not (root / ".jarvis_backups").exists()


def test_apply_requires_integration_sources_before_touching_app(tmp_path) -> None:
    root, original = _project(tmp_path)
    (root / "core" / "gui_voice_integration.py").unlink()

    with pytest.raises(RuntimeError, match="gui_voice_integration.py"):
        patcher.apply(root)

    assert (root / "app.py").read_text(encoding="utf-8") == original


def test_old_runtime_contract_is_rejected_before_app_is_modified(tmp_path) -> None:
    root, original = _project(tmp_path)
    (root / "core" / "conversation_runtime.py").write_text(
        "class ConversationRuntime:\n    def begin_turn(self, request): pass\n",
        encoding="utf-8",
    )

    inspection = patcher.inspect(root)
    assert inspection.compatible is False
    assert any("token_for" in issue for issue in inspection.missing_contracts)
    with pytest.raises(RuntimeError, match="çekirdek sözleşmesi"):
        patcher.apply(root)

    assert (root / "app.py").read_text(encoding="utf-8") == original
    assert not (root / ".jarvis_backups").exists()


def test_crlf_source_is_restored_byte_exactly(tmp_path: Path) -> None:
    root, _original = _project(tmp_path)
    app_path = root / "app.py"
    crlf = app_path.read_bytes().replace(b"\n", b"\r\n")
    app_path.write_bytes(crlf)

    patcher.apply(root)
    patcher.revert(root)

    assert app_path.read_bytes() == crlf
