from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.project_index import IndexedFile, ProjectIndex


def test_build_and_from_dict_accept_string_roots(tmp_path):
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    built = ProjectIndex.build(str(tmp_path))
    assert built.root == tmp_path.resolve()
    assert [item.relative_path for item in built.files] == ["main.py"]

    restored = ProjectIndex.from_dict(str(tmp_path), built.to_dict())
    assert restored.root == tmp_path.resolve()
    assert [item.relative_path for item in restored.files] == ["main.py"]


def test_apply_file_changes_ignores_unknown_change_kind(tmp_path):
    path = tmp_path / "main.py"
    path.write_text("print(1)", encoding="utf-8")
    index = ProjectIndex(root=tmp_path.resolve(), files=[IndexedFile("main.py", ".py", 8)])

    path.write_text("print(123456)", encoding="utf-8")
    index.apply_file_changes([("renamed-ish", Path("main.py"))])

    assert index.files[0].size == 8


def test_reconcile_snapshot_skips_malformed_states(tmp_path):
    path = tmp_path / "main.py"
    path.write_text("print(1)", encoding="utf-8")
    index = ProjectIndex.build(tmp_path)
    original = index.to_dict()

    repaired = index.reconcile_snapshot({
        Path("main.py"): ("bad", object()),
        Path("broken.py"): (1,),
        Path("other.py"): None,
    })

    # Invalid watcher entries must not delete or mutate otherwise valid index data.
    assert repaired == 0
    assert index.to_dict() == original


def test_reconcile_snapshot_normalizes_valid_numeric_state(tmp_path):
    path = tmp_path / "main.py"
    path.write_text("print(1)", encoding="utf-8")
    index = ProjectIndex(root=tmp_path.resolve())

    repaired = index.reconcile_snapshot({Path("main.py"): ("123", "8")})

    assert repaired == 1
    assert len(index.files) == 1
    assert index.files[0].relative_path == "main.py"
    assert index.files[0].size == path.stat().st_size
