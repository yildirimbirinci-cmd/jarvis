from __future__ import annotations

import json
from types import SimpleNamespace

from artmach_assistant.core import assistant as module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.model_roles import ModelRoleResolver


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps({"message": {"content": "{}"}}).encode("utf-8")


def test_own_code_proposal_uses_code_role_not_chat_role(monkeypatch, tmp_path) -> None:
    captured = {}

    monkeypatch.setattr(
        module,
        "OWN_CODE_CYCLE_FILE",
        tmp_path / "own_code_cycle.json",
    )

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response()

    class _Workspace:
        def set_workspace(self, _root):
            pass

        def invalidate_index(self):
            pass

        def call_graph_patch_context(self, *_args, **_kwargs):
            return SimpleNamespace(
                text="--- DOSYA: core/example.py ---\nVALUE = 1\n",
                used_call_graph=True,
            )

    class _Editor:
        pending = None

        def create_proposal(self, _payload):
            raise ValueError("stop after payload capture")

    config = SimpleNamespace(
        chat_model="fast-chat:3b",
        code_model="careful-code:14b",
        model="legacy-coder:7b",
        chat_context_window=2048,
        chat_max_output_tokens=256,
        code_context_window=16000,
        code_max_output_tokens=9000,
        ollama_url="http://127.0.0.1:11434",
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = config
    engine.model_roles = ModelRoleResolver(config)
    engine.workspace = _Workspace()
    engine.editor = _Editor()
    engine.project_memory = None
    engine.project_improvements = None
    engine.own_project_root = lambda: tmp_path

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = engine.prepare_own_code_proposal("example değerini güvenli değiştir")

    assert "yanıt veremedi" in result
    assert captured["payload"]["model"] == "careful-code:14b"
    assert captured["payload"]["model"] != "fast-chat:3b"
    assert captured["payload"]["options"]["num_ctx"] == 16000
    assert captured["payload"]["options"]["num_predict"] == 9000


def test_model_report_names_both_independent_roles() -> None:
    config = SimpleNamespace(
        chat_model="fast-chat:3b",
        code_model="careful-code:14b",
        model="legacy",
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = config
    engine.model_roles = ModelRoleResolver(config)

    report = engine._local_model_request("konuşma ve kod modellerinin adlarını söyle")

    assert "fast-chat:3b" in report
    assert "careful-code:14b" in report
    assert "Konuşma modelim" in report
    assert "Kod modelim" in report
