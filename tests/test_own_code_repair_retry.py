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


def test_single_attempt_structural_contract_rejection_is_terminal(monkeypatch, tmp_path) -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._validate_own_code_payload_shape = lambda raw: json.loads(raw)
    calls: list[str] = []

    def request(prompt, **_kwargs):
        calls.append(prompt)
        return json.dumps({
            "summary": "first",
            "files": [{
                "path": "core/research_manager.py",
                "reason": "repair",
                "operations": [{"op": "insert_after", "anchor": "x", "content": "y"}],
            }],
        })

    engine._request_code_model_json = request
    engine.editor = SimpleNamespace(reject=lambda: None)
    globals_map = AssistantEngine._generate_validated_own_code_proposal.__globals__
    workspace_error = globals_map["WorkspaceError"]

    engine.editor.create_proposal = lambda _raw: (_ for _ in ()).throw(
        workspace_error(
            "Çıkarılan blok replacement alanında `self.<yardımcı_metot>(...)` "
            "çağrısı zorunlu: core/research_manager.py işlem 2"
        )
    )
    for name in (
        "merge_duplicate_operation_rows",
        "ground_requested_docstring_replace_anchors",
        "repair_high_confidence_missing_anchors",
        "remove_redundant_noop_replaces",
        "qualify_inserted_private_helper_calls",
        "normalize_structural_class_method_insertions",
        "normalize_structural_method_block_replacements",
        "repair_unique_whitespace_anchors",
        "repair_ambiguous_replace_anchors",
        "reorder_insertions_after_exact_edits",
    ):
        monkeypatch.setitem(globals_map, name, lambda payload, **_kwargs: payload)
    monkeypatch.setitem(globals_map, "validate_behavior_preserving_extraction_payload", lambda *_args, **_kwargs: None)

    with pytest.raises(workspace_error):
        engine._generate_validated_own_code_proposal(
            "repair approved target",
            max_attempts=3,
            strict_attempt_limit=True,
        )

    assert len(calls) == 1


def test_strict_production_repair_is_exactly_one_model_call(monkeypatch, tmp_path) -> None:
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
    globals_map = AssistantEngine._generate_validated_own_code_proposal.__globals__
    workspace_error = globals_map["WorkspaceError"]
    engine.editor = SimpleNamespace(
        reject=lambda: None,
        create_proposal=lambda _raw: (_ for _ in ()).throw(
            workspace_error("SOURCE GROUNDED ANCHOR REDDI: core/assistant.py operation 1")
        ),
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

    with pytest.raises(workspace_error):
        engine._generate_validated_own_code_proposal(
            "repair approved target",
            max_attempts=99,
            strict_attempt_limit=False,
        )

    assert len(calls) == 1


def test_scope_rejection_is_terminal_in_single_pass_repair(monkeypatch, tmp_path) -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._validate_own_code_payload_shape = lambda raw: json.loads(raw)
    calls: list[str] = []

    source_path = tmp_path / "core" / "assistant.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "class AssistantEngine:\n"
        "    def handle(self, raw_text):\n"
        "        return raw_text\n",
        encoding="utf-8",
    )

    def request(prompt, **_kwargs):
        calls.append(prompt)
        return json.dumps({
            "summary": "bad scope",
            "files": [
                {"path": "core/assistant.py", "reason": "repair", "operations": [{"op": "replace", "old": "__ECHO_APPROVED_METHOD__", "new": "def handle(self, raw_text):\n    return raw_text"}]},
                {"path": "core/task_orchestrator.py", "reason": "wrong", "operations": [{"op": "replace", "old": "a", "new": "b"}]},
            ],
        })

    engine._request_code_model_json = request
    globals_map = AssistantEngine._generate_validated_own_code_proposal.__globals__
    workspace_error = globals_map["WorkspaceError"]
    engine.editor = SimpleNamespace(
        reject=lambda: None,
        create_proposal=lambda _raw: SimpleNamespace(summary="repair", files=()),
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
    monkeypatch.setitem(
        globals_map,
        "validate_behavior_preserving_extraction_payload",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(workspace_error):
        engine._generate_validated_own_code_proposal(
            "repair approved target\n"
            "DETERMINISTIC_REPAIR_ENVELOPE\n"
            "İzinli dosyalar: core/assistant.py\n"
            "Hedef semboller: AssistantEngine.handle\n"
            "APPROVED_STRUCTURAL_TARGET: AssistantEngine.handle",
            max_attempts=3,
            strict_attempt_limit=True,
        )

    assert len(calls) == 1


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


def test_symbol_scoped_placeholder_helper_rejection_is_terminal(monkeypatch, tmp_path) -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._validate_own_code_payload_shape = lambda raw: json.loads(raw)
    calls: list[str] = []

    def request(prompt, **_kwargs):
        calls.append(prompt)
        return json.dumps({
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
        })

    engine._request_code_model_json = request
    globals_map = AssistantEngine._generate_validated_own_code_proposal.__globals__
    workspace_error = globals_map["WorkspaceError"]
    engine.editor = SimpleNamespace(reject=lambda: None, create_proposal=lambda _raw: SimpleNamespace(summary="repair", files=()))
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

    with pytest.raises(workspace_error):
        engine._generate_validated_own_code_proposal(
            "repair approved target\n\nSEMBOL-KAPSAMLI PATCH KURALI:\nAPPROVED_STRUCTURAL_TARGET: AssistantEngine.handle",
            max_attempts=3,
            strict_attempt_limit=True,
        )

    assert len(calls) == 1


def test_runtime_repair_evidence_gate_detects_aggregate_method(tmp_path) -> None:
    source = tmp_path / "core" / "assistant.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class AssistantEngine:\n"
        "    def handle(self):\n"
        "        metadata = {\"aggregate_operation\": True}\n"
        "        return metadata\n",
        encoding="utf-8",
    )
    engine = object.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    finding = SimpleNamespace(
        category="repeated_slow_operation",
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
    )

    assert engine._runtime_target_requires_child_evidence(finding) is True


def test_runtime_repair_evidence_gate_blocks_without_child_source_evidence() -> None:
    engine = object.__new__(AssistantEngine)
    engine._development_root = lambda own_code=True: "/project"
    engine._runtime_event_service = lambda: SimpleNamespace(recent=lambda **_kwargs: ())
    finding = SimpleNamespace(
        category="repeated_slow_operation",
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
    )

    narrowed, report = engine._runtime_child_evidence_narrowing(finding)

    assert narrowed is None
    assert "INSUFFICIENT_EVIDENCE" in report
    assert "Patch izni: hayir" in report


def test_runtime_repair_evidence_gate_narrows_to_dominant_child_symbol() -> None:
    engine = object.__new__(AssistantEngine)
    engine._development_root = lambda own_code=True: "/project"

    def event(symbol: str, action: str, duration: float):
        return SimpleNamespace(
            status="completed",
            source_path="core/assistant.py",
            symbol=symbol,
            action=action,
            duration_ms=duration,
            metadata={"parent_action": "handle_command"},
        )

    events = (
        event("AssistantEngine.handle_local_command", "handle_local_command", 120.0),
        event("AssistantEngine.handle_local_command", "handle_local_command", 130.0),
        event("AssistantEngine.handle_local_command", "handle_local_command", 140.0),
        event("AssistantEngine.spoken_response", "spoken_response", 20.0),
        event("AssistantEngine.spoken_response", "spoken_response", 22.0),
        event("AssistantEngine.spoken_response", "spoken_response", 24.0),
    )
    engine._runtime_event_service = lambda: SimpleNamespace(
        recent=lambda **_kwargs: events
    )
    @dataclass
    class RuntimeFindingStub:
        category: str
        affected_paths: tuple[str, ...]
        affected_symbols: tuple[str, ...]

    finding = RuntimeFindingStub(
        category="repeated_slow_operation",
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
    )

    narrowed, report = engine._runtime_child_evidence_narrowing(finding)

    assert narrowed is not None
    assert narrowed.affected_paths == ("core/assistant.py",)
    assert narrowed.affected_symbols == ("AssistantEngine.handle_local_command",)
    assert "Durum: NARROWED" in report


def test_prepare_runtime_improvement_stops_before_session_when_evidence_gate_blocks(
    monkeypatch,
) -> None:
    engine = object.__new__(AssistantEngine)
    finding = SimpleNamespace(
        finding_id="RUN-06578E9EDE",
        category="repeated_slow_operation",
        occurrence_count=12,
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
    )
    decision = SimpleNamespace(can_prepare_plan=True)
    engine._find_runtime_finding = lambda _finding_id: finding
    engine._assess_runtime_repair_with_target_refresh = lambda value: (
        value,
        decision,
        "",
    )
    engine._runtime_target_requires_child_evidence = lambda _finding: True
    engine._runtime_child_evidence_narrowing = lambda _finding: (
        None,
        "REPAIR EVIDENCE GATE\nDurum: INSUFFICIENT_EVIDENCE\nPatch izni: hayir",
    )
    create_calls: list[object] = []
    engine._self_repair_store = lambda: SimpleNamespace(
        create=lambda **kwargs: create_calls.append(kwargs)
    )

    result = engine.prepare_runtime_improvement_implementation(
        finding.finding_id,
        repair_policy=decision,
    )

    assert "INSUFFICIENT_EVIDENCE" in result
    assert create_calls == []

def test_deterministic_repair_envelope_contract_is_present():
    import inspect
    from artmach_assistant.core.assistant import AssistantEngine

    source = inspect.getsource(AssistantEngine._generate_validated_own_code_proposal)
    assert "DETERMINISTIC_REPAIR_ENVELOPE" in source
    assert "__ECHO_APPROVED_METHOD__" in source
    assert "model anchor secemez" in source
    assert "base_attempts = 1" in source
    assert "attempts = 1" in source


def test_production_repair_prompt_removes_anchor_choice():
    import inspect
    from artmach_assistant.core.assistant import AssistantEngine

    source = inspect.getsource(AssistantEngine.prepare_own_code_proposal)
    assert "path, symbol, operation ve anchor secme yetkin yok" in source
    assert "old alani literal __ECHO_APPROVED_METHOD__ olmali" in source

def test_measure_handle_local_call_uses_real_callable_symbol_name():
    import inspect
    from artmach_assistant.core.assistant import AssistantEngine

    source = inspect.getsource(AssistantEngine._measure_handle_local_call)
    assert "getattr(function, '__name__', '')" in source
    assert 'symbol=f"AssistantEngine.{action}"' not in source


def test_private_runtime_target_keeps_leading_underscore():
    def _auto_research_world_fact():
        return None

    symbol = (
        f"AssistantEngine.{getattr(_auto_research_world_fact, '__name__', '')}"
        if str(getattr(_auto_research_world_fact, "__name__", "") or "").strip()
        else "AssistantEngine.auto_research_world_fact"
    )
    assert symbol == "AssistantEngine._auto_research_world_fact"

def test_runtime_child_symbol_is_canonicalized_against_live_ast(tmp_path):
    source = tmp_path / "core" / "assistant.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "class AssistantEngine:\n"
        "    def _auto_research_world_fact(self, text, resolved):\n"
        "        return None\n",
        encoding="utf-8",
    )
    engine = object.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path

    resolved = engine._canonical_runtime_source_symbol(
        "core/assistant.py",
        "AssistantEngine.auto_research_world_fact",
        "auto_research_world_fact",
    )
    assert resolved == "AssistantEngine._auto_research_world_fact"


def test_runtime_child_symbol_rejects_ambiguous_live_ast(tmp_path):
    source = tmp_path / "core" / "assistant.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "class AssistantEngine:\n"
        "    def auto_research_world_fact(self):\n"
        "        return None\n"
        "    def _auto_research_world_fact(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    engine = object.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path

    resolved = engine._canonical_runtime_source_symbol(
        "core/assistant.py",
        "AssistantEngine.missing",
        "auto_research_world_fact",
    )
    assert resolved == ""

