from __future__ import annotations

import json
from types import SimpleNamespace

from artmach_assistant.core.code_model_acceptance import (
    run_code_model_patch_acceptance,
)


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args) -> bytes:
        return json.dumps({"message": {"content": self.content}}).encode("utf-8")


def _config():
    return SimpleNamespace(
        code_model="coder:7b",
        chat_model="chat:3b",
        model="legacy",
        code_context_window=8192,
        code_max_output_tokens=4096,
        ollama_url="http://127.0.0.1:11434",
    )


def test_real_patch_contract_repairs_invalid_full_rewrite() -> None:
    replies = iter(
        (
            json.dumps(
                {
                    "summary": "bad",
                    "files": [
                        {
                            "path": "sample.py",
                            "reason": "bad",
                            "content": "def add(a, b):\n    return a + b\n",
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "summary": "fixed",
                    "files": [
                        {
                            "path": "sample.py",
                            "reason": "operator",
                            "operations": [
                                {
                                    "op": "replace",
                                    "old": "return a - b",
                                    "new": "return a + b",
                                }
                            ],
                        }
                    ],
                }
            ),
        )
    )
    prompts: list[str] = []

    def opener(request, **_kwargs):
        payload = json.loads(request.data.decode("utf-8"))
        prompts.append(payload["messages"][1]["content"])
        return _Response(next(replies))

    result = run_code_model_patch_acceptance(_config(), urlopen=opener)

    assert result.passed
    assert result.attempts == 2
    assert "tam içerikle yazılamaz" in prompts[1]


def test_repeated_invalid_response_is_bounded() -> None:
    invalid = json.dumps({"summary": "bad", "files": []})
    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response(invalid)

    result = run_code_model_patch_acceptance(_config(), urlopen=opener)

    assert not result.passed
    assert calls == 3
    assert result.attempts == 3
