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


def test_symbol_scope_retry_forbids_new_helper_without_explicit_extraction() -> None:
    prompt = build_validation_repair_prompt(
        "TaskOrchestrator.wrap.execute darboğazını düzelt",
        (
            "core/task_orchestrator.py [symbol_scope] onay disi sembol degisti: "
            "TaskOrchestrator._check_wrapper_overhead"
        ),
        {
            "summary": "new helper",
            "files": [
                {
                    "path": "core/task_orchestrator.py",
                    "reason": "repair",
                    "operations": [
                        {
                            "op": "insert_class_method",
                            "class_name": "TaskOrchestrator",
                            "content": "def _check_wrapper_overhead(self):\n    return True",
                        }
                    ],
                }
            ],
        },
        stage="sembol kapsami",
        targets=RepairTargets(
            paths=("core/task_orchestrator.py",),
            symbols=("TaskOrchestrator.wrap",),
            issue_codes=("symbol_scope",),
        ),
    )

    assert "insert_class_method, yeni sinif, yeni fonksiyon" in prompt
    assert "Yalniz HEDEF SEMBOLLER" in prompt
    assert "yardimciyi dogrudan self.<yardimci>" not in prompt


def test_single_attempt_gets_one_structural_contract_recovery_pass(monkeypatch, tmp_path) -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._validate_own_code_payload_shape = lambda raw: json.loads(raw)

    responses = iter((
        json.dumps({
            "summary": "first",
            "files": [{
                "path": "core/research_manager.py",
                "reason": "repair",
                "operations": [{"op": "insert_after", "anchor": "x", "content": "y"}],
            }],
        }),
        json.dumps({
            "summary": "second",
            "files": [{
                "path": "core/research_manager.py",
                "reason": "repair",
                "operations": [{"op": "insert_after", "anchor": "x", "content": "z"}],
            }],
        }),
    ))
    prompts: list[str] = []

    def request(prompt, **_kwargs):
        prompts.append(prompt)
        return next(responses)

    engine._request_code_model_json = request
    engine.editor = SimpleNamespace(
        create_proposal=lambda _raw: SimpleNamespace(
            summary="repair",
            files=(SimpleNamespace(path="core/research_manager.py"),),
        ),
        reject=lambda: None,
    )

    globals_map = AssistantEngine._generate_validated_own_code_proposal.__globals__
    workspace_error = globals_map["WorkspaceError"]
    structural_calls = {"count": 0}

    def structural(payload, **_kwargs):
        structural_calls["count"] += 1
        if structural_calls["count"] == 1:
            raise workspace_error(
                "Çıkarılan blok replacement alanında `self.<yardımcı_metot>(...)` "
                "çağrısı zorunlu: core/research_manager.py işlem 2"
            )
        return payload

    for name in (
        "merge_duplicate_operation_rows",
        "ground_requested_docstring_replace_anchors",
        "repair_high_confidence_missing_anchors",
        "remove_redundant_noop_replaces",
        "qualify_inserted_private_helper_calls",
        "normalize_structural_class_method_insertions",
        "repair_unique_whitespace_anchors",
        "repair_ambiguous_replace_anchors",
        "reorder_insertions_after_exact_edits",
    ):
        monkeypatch.setitem(globals_map, name, lambda payload, **_kwargs: payload)

    monkeypatch.setitem(globals_map, "normalize_structural_method_block_replacements", structural)
    monkeypatch.setitem(
        globals_map,
        "validate_behavior_preserving_extraction_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(
        globals_map,
        "build_structural_method_block_guidance",
        lambda **_kwargs: "",
    )

    proposal = engine._generate_validated_own_code_proposal(
        "repair approved target",
        max_attempts=1,
        strict_attempt_limit=True,
    )

    assert proposal.summary == "repair"
    assert len(prompts) == 2
    assert "STRUCTURAL CONTRACT RECOVERY (MANDATORY)" in prompts[1]
    assert "self.<yardımcı_metot>" in prompts[1]


def test_strict_production_repair_never_exceeds_two_model_calls(monkeypatch, tmp_path) -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._validate_own_code_payload_shape = lambda raw: json.loads(raw)
    calls: list[str] = []

    def request(prompt, **_kwargs):
        calls.append(prompt)
        return json.dumps({
            "summary": "bad",
            "files": [{
                "path": "core/assistant.py",
                "reason": "repair",
                "operations": [{"op": "replace", "old": "missing", "new": "x"}],
            }],
        })

    engine._request_code_model_json = request
    engine.editor = SimpleNamespace(reject=lambda: None)
    globals_map = AssistantEngine._generate_validated_own_code_proposal.__globals__
    workspace_error = globals_map["WorkspaceError"]
    engine.editor.create_proposal = lambda _raw: (_ for _ in ()).throw(
        workspace_error("SOURCE GROUNDED ANCHOR REDDI: core/assistant.py operation 1")
    )
    for name in (
        "merge_duplicate_operation_rows",
        "ground_requested_docstring_replace_anchors",
        "repair_high_confidence_missing_anchors",
        "repair_unique_whitespace_anchors",
        "remove_redundant_noop_replaces",
        "qualify_inserted_private_helper_calls",
        "normalize_structural_class_method_insertions",
        "normalize_structural_method_block_replacements",
        "repair_ambiguous_replace_anchors",
        "reorder_insertions_after_exact_edits",
    ):
        monkeypatch.setitem(globals_map, name, lambda payload, **_kwargs: payload)
    monkeypatch.setitem(globals_map, "validate_behavior_preserving_extraction_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(globals_map, "build_structural_method_block_guidance", lambda **_kwargs: "")
    monkeypatch.setitem(globals_map, "build_ambiguous_anchor_guidance", lambda *_args, **_kwargs: "EXACT LIVE SOURCE GUIDANCE")

    with pytest.raises(workspace_error):
        engine._generate_validated_own_code_proposal(
            "repair approved target",
            max_attempts=3,
            strict_attempt_limit=True,
        )

    assert len(calls) == 2


def test_bounded_recovery_scope_lock_clamps_mixed_out_of_scope_file(
    monkeypatch, tmp_path
) -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._validate_own_code_payload_shape = lambda raw: json.loads(raw)

    responses = iter((
        json.dumps({
            "summary": "first",
            "files": [{
                "path": "core/assistant.py",
                "reason": "repair",
                "operations": [{
                    "op": "replace_method_block",
                    "class_name": "AssistantEngine",
                    "method_name": "handle",
                    "block_test": "if runtime is not None:",
                    "replacement": "return None",
                }],
            }],
        }),
        json.dumps({
            "summary": "second",
            "files": [
                {
                    "path": "core/assistant.py",
                    "reason": "repair",
                    "operations": [{
                        "op": "replace",
                        "old": "unique approved source",
                        "new": "unique repaired source",
                    }],
                },
                {
                    "path": "core/task_orchestrator.py",
                    "reason": "wrong wrapper target",
                    "operations": [{
                        "op": "replace",
                        "old": "wrapper old",
                        "new": "wrapper new",
                    }],
                },
            ],
        }),
    ))
    prompts: list[str] = []

    def request(prompt, **_kwargs):
        prompts.append(prompt)
        return next(responses)

    engine._request_code_model_json = request
    captured: dict[str, object] = {}

    def create_proposal(raw):
        captured.update(json.loads(raw))
        return SimpleNamespace(
            summary="repair",
            files=(SimpleNamespace(path="core/assistant.py"),),
        )

    engine.editor = SimpleNamespace(create_proposal=create_proposal, reject=lambda: None)

    globals_map = AssistantEngine._generate_validated_own_code_proposal.__globals__
    workspace_error = globals_map["WorkspaceError"]
    structural_calls = {"count": 0}

    def structural(payload, **_kwargs):
        structural_calls["count"] += 1
        if structural_calls["count"] == 1:
            raise workspace_error(
                "Yapısal blok koşulu hedef metot ağacında tam olarak bir kez bulunmalı: "
                "core/assistant.py işlem 1; bulunan=8"
            )
        return payload

    for name in (
        "merge_duplicate_operation_rows",
        "ground_requested_docstring_replace_anchors",
        "repair_high_confidence_missing_anchors",
        "remove_redundant_noop_replaces",
        "qualify_inserted_private_helper_calls",
        "normalize_structural_class_method_insertions",
        "repair_unique_whitespace_anchors",
        "repair_ambiguous_replace_anchors",
        "reorder_insertions_after_exact_edits",
    ):
        monkeypatch.setitem(globals_map, name, lambda payload, **_kwargs: payload)

    monkeypatch.setitem(
        globals_map,
        "normalize_structural_method_block_replacements",
        structural,
    )
    monkeypatch.setitem(
        globals_map,
        "validate_behavior_preserving_extraction_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(
        globals_map,
        "build_structural_method_block_guidance",
        lambda **_kwargs: "",
    )
    monkeypatch.setitem(
        globals_map,
        "build_ambiguous_anchor_guidance",
        lambda *_args, **_kwargs: "CANDIDATE 1:\nunique approved source",
    )

    proposal = engine._generate_validated_own_code_proposal(
        "repair approved target\n"
        "İzinli dosyalar: core/assistant.py\n"
        "Hedef semboller: AssistantEngine.handle",
        max_attempts=1,
        strict_attempt_limit=True,
    )

    assert proposal.summary == "repair"
    assert len(prompts) == 2
    assert "RECOVERY SCOPE LOCK (MANDATORY)" in prompts[1]
    assert "core/assistant.py" in prompts[1]
    assert [row["path"] for row in captured["files"]] == ["core/assistant.py"]


def test_worktree_failure_gets_one_bounded_reproposal_and_revalidation(monkeypatch) -> None:
    engine = object.__new__(AssistantEngine)
    first = SimpleNamespace(
        summary="first",
        files=(SimpleNamespace(path="core/assistant.py", new_content="bad"),),
    )
    revised = SimpleNamespace(
        summary="revised",
        files=(SimpleNamespace(path="core/assistant.py", new_content="good"),),
    )
    editor = SimpleNamespace(pending=first)
    editor.reject = lambda: setattr(editor, "pending", None)
    engine.editor = editor
    engine._pending_own_code_fingerprint = "old"
    engine._clear_own_code_pending_proposal_store = lambda: None
    engine._run_own_tests = lambda: (True, "baseline ok")
    engine._test_failure_ids = lambda _output: set()
    engine._validate_own_code_at_root = lambda _root, baseline_failures=None: "ok"
    engine.own_project_root = lambda: "/project"

    prepare_calls: list[dict[str, object]] = []

    def prepare(instruction, **kwargs):
        prepare_calls.append({"instruction": instruction, **kwargs})
        editor.pending = revised
        engine._pending_own_code_fingerprint = "revised"
        return "revised proposal prepared"

    engine.prepare_own_code_proposal = prepare
    transitions: list[tuple[str, dict[str, object]]] = []
    store = SimpleNamespace(
        transition=lambda state, **kwargs: transitions.append((state, kwargs))
        or SimpleNamespace(state=state)
    )
    engine._self_repair_store = lambda: store

    class Validator:
        def __init__(self, root):
            assert root == "/project"

        def validate(self, proposal, callback):
            assert proposal is revised
            callback("/tmp/worktree")
            return SimpleNamespace(ok=True, output="24 passed")

    monkeypatch.setitem(
        AssistantEngine._recover_self_repair_worktree_failure.__globals__,
        "OwnCodeWorktreeValidator",
        Validator,
    )
    monkeypatch.setitem(
        AssistantEngine._recover_self_repair_worktree_failure.__globals__,
        "proposal_fingerprint",
        lambda proposal: "fp-revised" if proposal is revised else "fp-first",
    )

    session = SimpleNamespace(
        instruction="fix runtime finding",
        approved_paths=("core/assistant.py",),
        approved_symbols=("AssistantEngine.handle",),
        plan_id="RPR-06578E9EDE",
    )
    result = engine._recover_self_repair_worktree_failure(
        session,
        first,
        "FAILED tests/test_x.py::test_behavior",
    )

    assert len(prepare_calls) == 1
    assert prepare_calls[0]["production_repair"] is True
    assert prepare_calls[0]["repair_max_attempts"] == 0
    assert "FAILED tests/test_x.py::test_behavior" in prepare_calls[0]["instruction"]
    assert "REJECTED PROPOSAL JSON" in prepare_calls[0]["instruction"]
    assert editor.pending is revised
    assert transitions[-1][0] == "proposal_ready"
    assert transitions[-1][1]["expected"] == {"applying"}
    assert "worktree doğrulamasından geçti" in result
    assert "taslağı onayla" in result


def test_worktree_recovery_stops_after_revised_validation_failure(monkeypatch) -> None:
    engine = object.__new__(AssistantEngine)
    first = SimpleNamespace(
        summary="first",
        files=(SimpleNamespace(path="core/assistant.py", new_content="bad"),),
    )
    revised = SimpleNamespace(
        summary="revised",
        files=(SimpleNamespace(path="core/assistant.py", new_content="still bad"),),
    )
    editor = SimpleNamespace(pending=first)
    editor.reject = lambda: setattr(editor, "pending", None)
    engine.editor = editor
    engine._pending_own_code_fingerprint = "old"
    engine._clear_own_code_pending_proposal_store = lambda: None
    engine._run_own_tests = lambda: (True, "baseline ok")
    engine._test_failure_ids = lambda _output: set()
    engine._validate_own_code_at_root = lambda _root, baseline_failures=None: "bad"
    engine.own_project_root = lambda: "/project"
    calls = 0

    def prepare(_instruction, **_kwargs):
        nonlocal calls
        calls += 1
        editor.pending = revised
        return "revised proposal prepared"

    engine.prepare_own_code_proposal = prepare
    transitions: list[str] = []
    store = SimpleNamespace(
        transition=lambda state, **_kwargs: transitions.append(state)
        or SimpleNamespace(state=state)
    )
    engine._self_repair_store = lambda: store

    class Validator:
        def __init__(self, _root):
            pass

        def validate(self, _proposal, _callback):
            return SimpleNamespace(
                ok=False,
                output="FAILED tests/test_a.py::test_one\nFAILED tests/test_b.py::test_two",
            )

    monkeypatch.setitem(
        AssistantEngine._recover_self_repair_worktree_failure.__globals__,
        "OwnCodeWorktreeValidator",
        Validator,
    )

    session = SimpleNamespace(
        instruction="fix runtime finding",
        approved_paths=("core/assistant.py",),
        approved_symbols=("AssistantEngine.handle",),
        plan_id="RPR-06578E9EDE",
    )
    result = engine._recover_self_repair_worktree_failure(
        session,
        first,
        "initial worktree failure",
    )

    assert calls == 1
    assert editor.pending is None
    assert transitions[-1] == "proposal_failed"
    assert "Tek bounded worktree recovery pass" in result
    assert "FAILED tests/test_a.py::test_one" in result


def test_runtime_pipeline_regrounds_insert_anchors_immediately_before_create_proposal() -> None:
    import inspect

    source = inspect.getsource(AssistantEngine._generate_validated_own_code_proposal)
    marker = "payload = reorder_insertions_after_exact_edits("
    canonical = "canonical = json.dumps(payload, ensure_ascii=False)"
    start = source.index(marker)
    end = source.index(canonical, start)
    final_window = source[start:end]
    assert final_window.count("payload = repair_ambiguous_replace_anchors(") == 1
    assert "instruction=prompt" in final_window


def test_generic_approval_defers_active_self_repair_proposal_to_reserved_router() -> None:
    engine = object.__new__(AssistantEngine)
    engine.command_key = lambda _text: "taslagi onayla"
    engine._active_self_repair_session = lambda: SimpleNamespace(state="proposal_ready")
    engine.editor = SimpleNamespace(pending=SimpleNamespace())

    called = []
    engine.apply_pending_own_code_proposal = lambda: called.append(True) or "WRONG ROUTE"

    result = engine._own_code_approval_request("taslağı onayla")

    assert result is None
    assert called == []


def test_active_self_repair_apply_handler_owns_worktree_failure_recovery_source_contract() -> None:
    import inspect

    approval_source = inspect.getsource(AssistantEngine._own_code_approval_request)
    apply_source = inspect.getsource(AssistantEngine._apply_active_self_repair_proposal)

    assert "active_repair" in approval_source
    assert "proposal_ready" in approval_source
    assert "return None" in approval_source
    assert "_recover_self_repair_worktree_failure" in apply_source


def test_symbol_scoped_repair_rejects_placeholder_helper_and_recovers_in_method(
    monkeypatch, tmp_path
) -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._validate_own_code_payload_shape = lambda raw: json.loads(raw)

    responses = iter((
        json.dumps({
            "summary": "invented helper",
            "files": [{
                "path": "core/assistant.py",
                "reason": "repair",
                "operations": [{
                    "op": "insert_class_method",
                    "class_name": "AssistantEngine",
                    "content": "def _optimize_task_execution(self, task):\n    pass",
                }],
            }],
        }),
        json.dumps({
            "summary": "in method repair",
            "files": [{
                "path": "core/assistant.py",
                "reason": "repair",
                "operations": [{
                    "op": "replace",
                    "old": "runtime.raise_if_cancelled(turn_id)",
                    "new": "runtime.raise_if_cancelled(turn_id)  # bounded in-method repair",
                }],
            }],
        }),
    ))
    prompts: list[str] = []

    def request(prompt, **_kwargs):
        prompts.append(prompt)
        return next(responses)

    engine._request_code_model_json = request
    engine.editor = SimpleNamespace(
        create_proposal=lambda _raw: SimpleNamespace(
            summary="repair",
            files=(SimpleNamespace(path="core/assistant.py"),),
        ),
        reject=lambda: None,
    )

    globals_map = AssistantEngine._generate_validated_own_code_proposal.__globals__
    for name in (
        "merge_duplicate_operation_rows",
        "ground_requested_docstring_replace_anchors",
        "repair_high_confidence_missing_anchors",
        "repair_unique_whitespace_anchors",
        "remove_redundant_noop_replaces",
        "qualify_inserted_private_helper_calls",
        "normalize_structural_class_method_insertions",
        "normalize_structural_method_block_replacements",
        "repair_ambiguous_replace_anchors",
        "reorder_insertions_after_exact_edits",
    ):
        monkeypatch.setitem(globals_map, name, lambda payload, **_kwargs: payload)
    monkeypatch.setitem(
        globals_map,
        "validate_behavior_preserving_extraction_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(
        globals_map,
        "build_structural_method_block_guidance",
        lambda **_kwargs: "",
    )

    proposal = engine._generate_validated_own_code_proposal(
        "repair approved target\n\nSEMBOL-KAPSAMLI PATCH KURALI:\n"
        "APPROVED_STRUCTURAL_TARGET: AssistantEngine.handle",
        max_attempts=1,
        strict_attempt_limit=True,
    )

    assert proposal.summary == "repair"
    assert len(prompts) == 2
    assert "SOURCE-GROUNDED HELPER ELIGIBILITY" in prompts[1]
    assert "Do not emit insert_class_method" in prompts[1]
