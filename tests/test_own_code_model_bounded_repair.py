from __future__ import annotations

import json
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.model_roles import ModelRoleResolver
from artmach_assistant.core.workspace import WorkspaceService


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args) -> bytes:
        return json.dumps({"message": {"content": self.content}}).encode("utf-8")


def _engine(tmp_path) -> AssistantEngine:
    source = tmp_path / "core" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n\ndef read_value():\n    return VALUE\n", encoding="utf-8")
    workspace = WorkspaceService(tmp_path)
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = SimpleNamespace(
        chat_model="chat:3b",
        code_model="code:7b",
        model="code:7b",
        chat_context_window=2048,
        chat_max_output_tokens=256,
        code_context_window=16000,
        code_max_output_tokens=6000,
        ollama_url="http://127.0.0.1:11434",
    )
    engine.model_roles = ModelRoleResolver(engine.config)
    engine.workspace = workspace
    engine.editor = EditManager(workspace)
    engine.project_memory = None
    engine.project_improvements = None
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._load_own_code_plan = lambda: None
    engine._load_own_validation = lambda: (True, "")
    engine._save_own_code_cycle = lambda *_args, **_kwargs: None
    return engine


def test_invalid_full_file_rewrite_is_repaired_with_exact_operation(
    tmp_path, monkeypatch
) -> None:
    engine = _engine(tmp_path)
    responses = iter(
        (
            json.dumps(
                {
                    "summary": "unsafe full rewrite",
                    "files": [
                        {
                            "path": "core/example.py",
                            "reason": "value",
                            "content": "VALUE = 2\n",
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "summary": "safe exact edit",
                    "files": [
                        {
                            "path": "core/example.py",
                            "reason": "value",
                            "operations": [
                                {"op": "replace", "old": "VALUE = 1", "new": "VALUE = 2"}
                            ],
                        }
                    ],
                }
            ),
        )
    )
    prompts: list[str] = []

    def fake_urlopen(request, **_kwargs):
        payload = json.loads(request.data.decode("utf-8"))
        prompts.append(payload["messages"][1]["content"])
        return _Response(next(responses))

    monkeypatch.setattr(
        "artmach_assistant.core.assistant.urllib.request.urlopen", fake_urlopen
    )

    result = engine.prepare_own_code_proposal(
        "VALUE değerini iki yap.",
        approved_paths=("core/example.py",),
        plan_id="RPR-1234567890",
    )

    assert "Kod değişikliği önerisini hazırladım" in result
    assert len(prompts) == 2
    assert "tam içerikle yeniden yazılamaz" in prompts[1]
    assert engine.editor.pending is not None
    assert engine.editor.pending.files[0].new_content.startswith("VALUE = 2")
    assert "def read_value" in engine.editor.pending.files[0].new_content


def test_same_invalid_patch_is_not_repeated_forever(tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path)
    invalid = json.dumps(
        {
            "summary": "unsafe full rewrite",
            "files": [
                {
                    "path": "core/example.py",
                    "reason": "value",
                    "content": "VALUE = 2\n",
                }
            ],
        }
    )
    calls = 0

    def fake_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response(invalid)

    monkeypatch.setattr(
        "artmach_assistant.core.assistant.urllib.request.urlopen", fake_urlopen
    )

    result = engine.prepare_own_code_proposal(
        "VALUE değerini iki yap.",
        approved_paths=("core/example.py",),
        plan_id="RPR-1234567890",
    )

    assert calls == 3
    assert "3 kontrollü denemede" in result
    assert engine.editor.pending is None
