from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.model_roles import ModelRoleResolver
from artmach_assistant.core.workspace import WorkspaceError, WorkspaceService


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


def test_all_raw_ollama_attempts_are_preserved_for_diagnosis(
    tmp_path, monkeypatch
) -> None:
    engine = _engine(tmp_path)
    diagnostic_root = tmp_path / "user-data"
    monkeypatch.setitem(
        engine._generate_validated_own_code_proposal.__func__.__globals__,
        "DATA_DIR",
        diagnostic_root,
    )
    first = json.dumps({
        "summary": "first raw",
        "files": [{
            "path": "core/example.py",
            "content": "VALUE = 2\n",
        }],
    })
    second = json.dumps({
        "summary": "second raw",
        "files": [{
            "path": "core/example.py",
            "operations": [{
                "op": "replace",
                "old": "MISSING = 1",
                "new": "VALUE = 2",
            }],
        }],
    })
    responses = iter((first, second, second))
    engine._request_code_model_json = lambda *_args, **_kwargs: next(responses)

    with pytest.raises(WorkspaceError, match="3 kontrollü denemede"):
        engine._generate_validated_own_code_proposal("tanılama testi")

    log_path = (
        diagnostic_root / "diagnostics" / "own_code_model_raw_attempts.json"
    )
    payload = json.loads(log_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["attempt_limit"] == 3
    assert [row["attempt"] for row in payload["attempts"]] == [1, 2, 3]
    assert [row["raw_model_response"] for row in payload["attempts"]] == [
        first,
        second,
        second,
    ]
    assert [row["outcome"] for row in payload["attempts"]] == [
        "rejected_validation",
        "rejected_validation",
        "rejected_duplicate",
    ]
    assert all(row["validation_error"] for row in payload["attempts"])


def test_real_edit_survives_redundant_noop_on_second_attempt(tmp_path) -> None:
    engine = _engine(tmp_path)
    responses = iter((
        json.dumps({
            "summary": "ambiguous first draft",
            "files": [{
                "path": "core/example.py",
                "operations": [
                    {"op": "replace", "old": "VALUE", "new": "CURRENT_VALUE"}
                ],
            }],
        }),
        json.dumps({
            "summary": "valid draft with redundant no-op",
            "files": [{
                "path": "core/example.py",
                "operations": [
                    {"op": "replace", "old": "VALUE = 1", "new": "VALUE = 1"},
                    {"op": "replace", "old": "VALUE = 1", "new": "VALUE = 2"},
                ],
            }],
        }),
    ))
    calls = 0

    def respond(_prompt: str, **_kwargs) -> str:
        nonlocal calls
        calls += 1
        return next(responses)

    engine._request_code_model_json = respond

    proposal = engine._generate_validated_own_code_proposal(
        "core/example.py içindeki VALUE değerini değiştir"
    )

    assert calls == 2
    assert proposal.files[0].new_content.startswith("VALUE = 2")


def test_helper_insertion_cannot_make_following_exact_edit_ambiguous(tmp_path) -> None:
    engine = _engine(tmp_path)
    (tmp_path / "app.py").write_text(
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        command = self.listen_dialogue()\n"
        "        return command\n"
        "\n"
        "class NextWorker:\n"
        "    pass\n",
        encoding="utf-8",
    )
    raw = json.dumps({
        "summary": "extract dialogue helper",
        "files": [{
            "path": "app.py",
            "operations": [
                {
                    "op": "insert_before",
                    "anchor": "class NextWorker:\n",
                    "content": (
                        "    def _listen_dialogue_command(self):\n"
                        "        command = self.listen_dialogue()\n"
                        "        return command\n\n"
                    ),
                },
                {
                    "op": "replace",
                    "old": "        command = self.listen_dialogue()\n",
                    "new": "        command = self._listen_dialogue_command()\n",
                },
            ],
        }],
    })
    calls = 0

    def respond(_prompt: str, **_kwargs) -> str:
        nonlocal calls
        calls += 1
        return raw

    engine._request_code_model_json = respond
    proposal = engine._generate_validated_own_code_proposal(
        "app.py içindeki WakeWordWorker.run metodunu refaktör et"
    )

    assert calls == 1
    rendered = proposal.files[0].new_content
    assert "command = self._listen_dialogue_command()" in rendered
    assert "def _listen_dialogue_command" in rendered


def test_duplicate_missing_anchor_changes_next_retry_prompt(tmp_path) -> None:
    engine = _engine(tmp_path)
    (tmp_path / "app.py").write_text(
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        while self.running:\n"
        "            self.listen_wake()\n",
        encoding="utf-8",
    )
    invalid = json.dumps(
        {
            "summary": "dialogue helper",
            "files": [
                {
                    "path": "app.py",
                    "reason": "refactor",
                    "operations": [
                        {
                            "op": "replace",
                            "old": "        command = self.listen_dialogue()\n",
                            "new": "        command = self._listen_dialogue_command()\n",
                        }
                    ],
                }
            ],
        }
    )
    prompts: list[str] = []

    def repeat_invalid(prompt: str, **_kwargs) -> str:
        prompts.append(prompt)
        return invalid

    engine._request_code_model_json = repeat_invalid

    with pytest.raises(WorkspaceError, match="3 kontrollü denemede"):
        engine._generate_validated_own_code_proposal(
            "app.py dosyasındaki WakeWordWorker.run metodunu refaktör et"
        )

    assert len(prompts) == 3
    assert prompts[1] != prompts[2]
    assert "aynısını tekrar üretti" in prompts[2]
    assert "aynı old/anchor değerlerini yeniden kullanma" in prompts[2]


def test_duplicate_after_noop_keeps_actionable_validator_error(tmp_path) -> None:
    engine = _engine(tmp_path)
    first = json.dumps(
        {
            "summary": "ambiguous draft",
            "files": [
                {
                    "path": "core/example.py",
                    "reason": "refactor",
                    "operations": [
                        {"op": "replace", "old": "VALUE", "new": "CURRENT_VALUE"}
                    ],
                }
            ],
        }
    )
    noop = json.dumps(
        {
            "summary": "no-op draft",
            "files": [
                {
                    "path": "core/example.py",
                    "reason": "refactor",
                    "operations": [
                        {"op": "replace", "old": "VALUE = 1", "new": "VALUE = 1"}
                    ],
                }
            ],
        }
    )
    responses = iter((first, noop, first))
    prompts: list[str] = []

    def respond(prompt: str, **_kwargs) -> str:
        prompts.append(prompt)
        return next(responses)

    engine._request_code_model_json = respond

    with pytest.raises(WorkspaceError, match="3 kontrollü denemede"):
        engine._generate_validated_own_code_proposal(
            "core/example.py içindeki değeri refaktör et"
        )

    assert len(prompts) == 3
    assert "Patch işlemi gerçek değişiklik üretmedi" in prompts[2]
    assert "NO-OP OPERASYONU TEKRARLAMA" in prompts[2]
