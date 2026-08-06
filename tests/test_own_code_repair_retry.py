from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_repair_retry import (
    RepairRetryPolicy,
    RepairTargets,
    build_semantic_repair_prompt,
    build_validation_repair_prompt,
    extract_repair_targets,
    merge_targeted_repair_response,
)


@dataclass
class Change:
    path: str
    reason: str = "fix"
    new_content: str = ""


@dataclass
class Proposal:
    summary: str = "repair"
    files: tuple[Change, ...] = (
        Change("app.py", new_content="class App: pass\n"),
    )


def test_semantic_report_is_fed_back_to_repair_model() -> None:
    prompt = build_semantic_repair_prompt(
        "uygulamayı düzelt",
        "app.py: App.run sembolü kayboldu",
        Proposal(),
    )
    assert "App.run sembolü kayboldu" in prompt
    assert "Aynı hatalı patch'i tekrarlama" in prompt
    assert "diğer sembol ve davranışları koru" in prompt
    assert '"path": "app.py"' in prompt


def test_semantic_prompt_explains_extraction_control_flow_preservation() -> None:
    prompt = build_semantic_repair_prompt(
        "Bloğu davranışı değiştirmeden yardımcı metoda çıkar.",
        "app.py: gözlenebilir işlem kaybı (assign:self._next_mode, "
        "call:self.msleep, control:break, control:continue)",
        Proposal(),
    )

    assert "her `assign:`, `call:` ve `control:` öğesini koru" in prompt
    assert "helper bir karar değeri döndürmeli" in prompt
    assert "aynı `break`/`continue` kararını vermelidir" in prompt


def test_target_extraction_limits_repair_to_reported_file_and_symbol() -> None:
    proposal = Proposal(files=(
        Change("core/a.py", new_content="def keep():\n    return 1\n"),
        Change("core/b.py", new_content="def run():\n    return 2\n"),
    ))

    targets = extract_repair_targets(
        "Semantik koruma reddi: core/b.py: beklenmeyen genel sembol kaybı (Worker.run)",
        proposal,
    )

    assert targets.paths == ("core/b.py",)
    assert targets.symbols == ("Worker.run",)
    assert not targets.used_fallback


def test_patch_issue_code_and_line_are_in_bounded_prompt() -> None:
    proposal = Proposal(files=(
        Change("core/a.py", new_content="def value(:\n"),
        Change("core/b.py", new_content="VALUE = 2\n"),
    ))
    report = "Patch doğrulaması başarısız:\ncore/a.py:1 [python_syntax] invalid syntax"
    targets = extract_repair_targets(report, proposal)

    prompt = build_validation_repair_prompt(
        "Sözdizimi hatasını düzelt.",
        report,
        proposal,
        stage="patch doğrulaması",
        targets=targets,
    )

    assert targets.paths == ("core/a.py",)
    assert targets.issue_codes == ("python_syntax",)
    assert "core/a.py" in prompt
    assert '"path": "core/b.py"' not in prompt


def test_targeted_merge_preserves_unaffected_files() -> None:
    rejected = Proposal(files=(
        Change("core/a.py", reason="a", new_content="def a(:\n"),
        Change("core/b.py", reason="b", new_content="VALUE = 2\n"),
    ))
    targets = extract_repair_targets(
        "core/a.py:1 [python_syntax] invalid syntax",
        rejected,
    )
    repaired = {
        "summary": "Sözdizimi düzeltildi",
        "files": [
            {
                "path": "core/a.py",
                "reason": "syntax",
                "content": "def a():\n    return 1\n",
            }
        ],
    }

    merged = json.loads(
        merge_targeted_repair_response(rejected, repaired, targets)
    )

    assert [row["path"] for row in merged["files"]] == ["core/a.py", "core/b.py"]
    assert merged["files"][0]["content"] == "def a():\n    return 1\n"
    assert merged["files"][1]["content"] == "VALUE = 2\n"


def test_targeted_merge_rejects_scope_expansion() -> None:
    rejected = Proposal(files=(
        Change("core/a.py", new_content="def a(:\n"),
        Change("core/b.py", new_content="VALUE = 2\n"),
    ))
    targets = extract_repair_targets(
        "core/a.py:1 [python_syntax] invalid syntax",
        rejected,
    )

    with pytest.raises(ValueError, match="izin verilmeyen dosya"):
        merge_targeted_repair_response(
            rejected,
            {
                "summary": "scope expanded",
                "files": [
                    {"path": "core/a.py", "reason": "fix", "content": "def a():\n    pass\n"},
                    {"path": "core/b.py", "reason": "change", "content": "VALUE = 3\n"},
                ],
            },
            targets,
        )


def test_targeted_merge_rejects_identical_retry() -> None:
    rejected = Proposal(files=(
        Change("core/a.py", new_content="def a(:\n"),
    ))
    targets = extract_repair_targets(
        "core/a.py:1 [python_syntax] invalid syntax",
        rejected,
    )

    with pytest.raises(ValueError, match="aynı patch"):
        merge_targeted_repair_response(
            rejected,
            {
                "summary": "unchanged",
                "files": [
                    {"path": "core/a.py", "reason": "same", "content": "def a(:\n"},
                ],
            },
            targets,
        )


def test_policy_bounds_attempts() -> None:
    assert RepairRetryPolicy().max_attempts == 3
    with pytest.raises(ValueError):
        RepairRetryPolicy(max_attempts=4)


def test_semantic_repair_retries_semantically_invalid_candidate(monkeypatch) -> None:
    engine = object.__new__(AssistantEngine)
    engine._own_code_repair_policy = RepairRetryPolicy(max_attempts=3)
    history_rows: list[tuple[str, dict[str, object]]] = []
    engine.own_code_history = SimpleNamespace(
        record=lambda event, **fields: history_rows.append((event, fields))
    )
    engine.editor = SimpleNamespace(reject=lambda: None)
    candidates = [
        SimpleNamespace(marker="first", files=(SimpleNamespace(path="app.py"),)),
        SimpleNamespace(marker="second", files=(SimpleNamespace(path="app.py"),)),
    ]
    repair_calls: list[tuple[object, str]] = []

    def request(_instruction, rejected, report, **_kwargs):
        repair_calls.append((rejected, report))
        return candidates[len(repair_calls) - 1]

    validations = iter((
        SimpleNamespace(valid=False, report=lambda: "lost call:self.msleep"),
        SimpleNamespace(valid=True, report=lambda: "ok"),
    ))
    monkeypatch.setattr(engine, "_request_targeted_validation_repair", request)
    monkeypatch.setitem(
        AssistantEngine._repair_semantic_proposal.__globals__,
        "validate_semantic_replacement",
        lambda *_args: next(validations),
    )
    original = SimpleNamespace(files=(SimpleNamespace(path="app.py"),))

    repaired = engine._repair_semantic_proposal(
        "davranışı değiştirmeden çıkar",
        original,
        "lost assign:self._next_mode",
    )

    assert repaired.marker == "second"
    assert len(repair_calls) == 2
    assert repair_calls[0] == (original, "lost assign:self._next_mode")
    assert repair_calls[1] == (candidates[0], "lost call:self.msleep")
    assert history_rows[-1][0] == "semantik patch otomatik onarıldı"
    assert history_rows[-1][1]["deneme"] == 2


def test_first_targeted_semantic_request_includes_structural_source_guidance(
    monkeypatch,
) -> None:
    engine = object.__new__(AssistantEngine)
    engine.config = SimpleNamespace(
        model="local-model",
        code_model="",
        ollama_url="http://127.0.0.1:11434",
    )
    engine._own_code_repair_policy = RepairRetryPolicy(max_attempts=1)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.editor = SimpleNamespace(
        create_proposal=lambda raw: SimpleNamespace(
            summary="repair",
            files=(SimpleNamespace(path="app.py", new_content="fixed"),),
        )
    )
    engine.own_project_root = lambda: "/project"
    engine._model_role_resolver = lambda: SimpleNamespace(
        code=SimpleNamespace(
            model="local-model", context_window=32768, max_output_tokens=8192
        )
    )
    monkeypatch.setitem(
        AssistantEngine._request_targeted_validation_repair.__globals__,
        "build_structural_method_block_guidance",
        lambda **_kwargs: "PROVEN COMPLETE AST BLOCK",
    )
    requested_prompts: list[str] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "message": {
                    "content": json.dumps({
                        "summary": "repair",
                        "files": [{
                            "path": "app.py",
                            "reason": "fix",
                            "content": "fixed",
                        }],
                    })
                }
            }).encode("utf-8")

    def fake_urlopen(request, **_kwargs):
        body = json.loads(request.data.decode("utf-8"))
        requested_prompts.append(body["messages"][1]["content"])
        return _Response()

    monkeypatch.setattr(
        "artmach_assistant.core.assistant.urllib.request.urlopen",
        fake_urlopen,
    )
    rejected = Proposal(files=(Change("app.py", new_content="broken"),))

    repaired = engine._request_targeted_validation_repair(
        "app.py içindeki WakeWordWorker.run aktif diyalog bloğunu "
        "davranışı değiştirmeden yardımcı metoda çıkar",
        rejected,
        "app.py: gözlenebilir işlem kaybı (call:self.msleep)",
        stage="semantik koruma",
    )

    assert repaired is not None
    assert len(requested_prompts) == 1
    assert "PROVEN COMPLETE AST BLOCK" in requested_prompts[0]



def test_assistant_repair_request_merges_only_validator_target(monkeypatch) -> None:
    engine = object.__new__(AssistantEngine)
    engine.config = SimpleNamespace(
        model="local-model",
        code_model="",
        ollama_url="http://127.0.0.1:11434",
    )
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)

    merged_payloads: list[dict[str, object]] = []

    def create_proposal(raw: str):
        payload = json.loads(raw)
        merged_payloads.append(payload)
        return SimpleNamespace(
            summary=payload["summary"],
            files=[
                SimpleNamespace(path=row["path"], new_content=row["content"])
                for row in payload["files"]
            ],
        )

    engine.editor = SimpleNamespace(create_proposal=create_proposal)
    rejected = Proposal(files=(
        Change("core/a.py", reason="syntax", new_content="def a(:\n"),
        Change("core/b.py", reason="unrelated", new_content="VALUE = 2\n"),
    ))
    model_payload = {
        "message": {
            "content": json.dumps(
                {
                    "summary": "Sözdizimi düzeltildi",
                    "files": [
                        {
                            "path": "core/a.py",
                            "reason": "syntax",
                            "content": "def a():\n    return 1\n",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        }
    }
    requested_prompts: list[str] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(model_payload, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, **_kwargs):
        body = json.loads(request.data.decode("utf-8"))
        requested_prompts.append(body["messages"][1]["content"])
        return _Response()

    monkeypatch.setattr(
        "artmach_assistant.core.assistant.urllib.request.urlopen",
        fake_urlopen,
    )

    repaired = engine._request_targeted_validation_repair(
        "Sözdizimi hatasını düzelt.",
        rejected,
        "Patch doğrulaması başarısız:\ncore/a.py:1 [python_syntax] invalid syntax",
        stage="patch doğrulaması",
    )

    assert repaired is not None
    assert len(requested_prompts) == 1
    assert '"path": "core/a.py"' in requested_prompts[0]
    assert '"path": "core/b.py"' not in requested_prompts[0]
    assert [row["path"] for row in merged_payloads[0]["files"]] == [
        "core/a.py",
        "core/b.py",
    ]
    assert merged_payloads[0]["files"][1]["content"] == "VALUE = 2\n"

def test_symbol_scope_retry_requires_private_helper_to_be_called_or_removed() -> None:
    proposal = {
        "summary": "unused helper",
        "files": [
            {
                "path": "core/voice_service.py",
                "reason": "repair",
                "content": (
                    "class VoiceService:\n"
                    "    def recognize_wav(self, path):\n"
                    "        return path\n\n"
                    "    def _check_repeated_tokens(self, tokens):\n"
                    "        return len(tokens) > 3\n"
                ),
            }
        ],
    }
    targets = RepairTargets(
        paths=("core/voice_service.py",),
        symbols=(
            "VoiceService.recognize_wav",
            "VoiceService._check_repeated_tokens",
        ),
    )

    prompt = build_validation_repair_prompt(
        "VoiceService.recognize_wav hatasini duzelt",
        (
            "core/voice_service.py [symbol_scope] "
            "onay disi sembol degisti: "
            "VoiceService._check_repeated_tokens"
        ),
        proposal,
        stage="sembol kapsami",
        targets=targets,
    )

    assert "SEMBOL KAPSAMI ONARIM KURALI" in prompt
    assert "yardimciyi tamamen kaldir" in prompt
    assert "self.<yardimci>(...)" in prompt
    assert "Cagirilmayan veya bagimsiz" in prompt


def test_non_symbol_retry_does_not_receive_symbol_scope_guidance() -> None:
    proposal = {
        "summary": "anchor repair",
        "files": [
            {
                "path": "core/example.py",
                "reason": "repair",
                "content": "VALUE = 2\n",
            }
        ],
    }

    prompt = build_validation_repair_prompt(
        "degeri duzelt",
        "Patch anchor bulunamadi",
        proposal,
        stage="anchor",
        targets=RepairTargets(
            paths=("core/example.py",),
        ),
    )

    assert "SEMBOL KAPSAMI ONARIM KURALI" not in prompt

def test_symbol_scope_retry_targets_only_approved_symbols() -> None:
    extracted = RepairTargets(
        paths=("core/voice_service.py",),
        symbols=("VoiceService._check_repeated_run",),
        issue_codes=("symbol_scope",),
    )
    approved_paths = ("core/voice_service.py",)
    approved_symbols = ("VoiceService.recognize_wav",)

    targets = RepairTargets(
        paths=tuple(dict.fromkeys((
            *approved_paths,
            *extracted.paths,
        ))),
        symbols=tuple(approved_symbols),
        issue_codes=extracted.issue_codes,
        used_fallback=extracted.used_fallback,
    )

    prompt = build_validation_repair_prompt(
        "VoiceService.recognize_wav hatasini duzelt",
        (
            "core/voice_service.py [symbol_scope] "
            "onay disi sembol degisti: "
            "VoiceService._check_repeated_run"
        ),
        {
            "summary": "unused helper",
            "files": [
                {
                    "path": "core/voice_service.py",
                    "reason": "repair",
                    "content": "class VoiceService:\\n    pass\\n",
                }
            ],
        },
        stage="sembol kapsami",
        targets=targets,
    )

    prompt_lines = prompt.splitlines()
    heading_index = prompt_lines.index("HEDEF SEMBOLLER:")
    target_lines: list[str] = []

    for line in prompt_lines[heading_index + 1:]:
        if line.startswith("- "):
            target_lines.append(line[2:])
            continue
        if target_lines:
            break

    assert target_lines == [
        "VoiceService.recognize_wav",
    ]
    assert "VoiceService._check_repeated_run" in prompt
    assert "yardimciyi tamamen kaldir" in prompt


def test_structural_target_retry_explains_direct_method_and_exact_replace() -> None:
    proposal = {
        "summary": "bad structural target",
        "files": [{
            "path": "core/task_orchestrator.py",
            "reason": "repair",
            "content": "",
        }],
    }
    prompt = build_validation_repair_prompt(
        "TaskOrchestrator.wrap.execute icin taslak hazirla",
        (
            "Yapısal blok hedefi onaylı sembolle eşleşmiyor: "
            "core/task_orchestrator.py işlem 2"
        ),
        proposal,
        stage="yapısal blok",
        targets=RepairTargets(paths=("core/task_orchestrator.py",)),
    )

    assert "YAPISAL HEDEF ONARIM KURALI" in prompt
    assert "`wrap.execute_task` gibi noktalı" in prompt
    assert "küçük ve tam eşleşen `replace`" in prompt
    assert "`_execute_task` gibi yeni çağrılar icat etme" in prompt
