from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.project_index import ProjectIndex


def test_project_index_excludes_runtime_checkpoint_tree(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "assistant.py").write_text("x = 1\n", encoding="utf-8")
    checkpoint = (
        tmp_path
        / ".artmach_assistant"
        / "checkpoints"
        / "stamp"
        / "after"
        / "core"
    )
    checkpoint.mkdir(parents=True)
    (checkpoint / "assistant.py").write_text("x = 2\n", encoding="utf-8")

    index = ProjectIndex.build(tmp_path)
    paths = {item.relative_path.replace(chr(92), "/") for item in index.files}

    assert "core/assistant.py" in paths
    assert not any(".artmach_assistant" in path for path in paths)


def test_candidate_resolver_rejects_checkpoint_paths(tmp_path: Path) -> None:
    active = tmp_path / "core"
    active.mkdir()
    (active / "assistant.py").write_text("x = 1\n", encoding="utf-8")
    checkpoint = (
        tmp_path
        / ".artmach_assistant"
        / "checkpoints"
        / "stamp"
        / "after"
        / "core"
    )
    checkpoint.mkdir(parents=True)
    (checkpoint / "assistant.py").write_text("x = 2\n", encoding="utf-8")

    context = SimpleNamespace(
        text=(
            "DOSYA: .artmach_assistant/checkpoints/stamp/after/core/assistant.py | symbol\n"
            "DOSYA: core/assistant.py | symbol\n"
        )
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine.workspace = SimpleNamespace(
        set_workspace=lambda _root: None,
        call_graph_patch_context=lambda *_args, **_kwargs: context,
    )

    assert engine._resolve_own_code_candidate_paths("assistant refactor") == (
        "core/assistant.py",
    )


def test_active_source_guard_rejects_state_and_backup_trees() -> None:
    guard = AssistantEngine._is_active_own_code_source_path
    assert guard("core/assistant.py")
    assert not guard(".artmach_assistant/checkpoints/x/after/core/assistant.py")
    assert not guard(".jarvis/checkpoints/x/core/assistant.py")
    assert not guard(".jarvis_fix_backup/x/core/assistant.py")
