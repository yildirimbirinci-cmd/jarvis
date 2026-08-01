from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import types

import pytest


def _install_stubs(tmp_path: Path) -> None:
    config = types.ModuleType("artmach_assistant.config")
    config.DATA_DIR = tmp_path / "data"
    sys.modules[config.__name__] = config

    normalizer = types.ModuleType("artmach_assistant.core.path_normalizer")
    normalizer.normalize_project_root = lambda value: Path(value).expanduser().resolve(strict=False)
    normalizer.path_key = lambda value: os.path.normcase(
        os.path.normpath(str(Path(value).expanduser().resolve(strict=False)))
    )
    sys.modules[normalizer.__name__] = normalizer

    validation = types.ModuleType("artmach_assistant.core.store_validation")
    def atomic_write_json(path: Path, payload: dict, *, max_bytes: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    validation.atomic_write_json = atomic_write_json
    validation.read_json_object = lambda path, *, max_bytes: json.loads(path.read_text(encoding="utf-8"))
    validation.require_schema_version = lambda payload, *, field, expected: None
    sys.modules[validation.__name__] = validation

    project_index = types.ModuleType("artmach_assistant.core.project_index")
    class ProjectIndex:
        def __init__(self, root: Path) -> None:
            self.root = root
        def to_dict(self) -> dict:
            return {}
        @classmethod
        def from_dict(cls, root: Path, payload: dict):
            return cls(root)
    project_index.ProjectIndex = ProjectIndex
    sys.modules[project_index.__name__] = project_index


@pytest.mark.parametrize(
    ("module_name", "class_name", "save_kind"),
    [
        ("artmach_assistant.core.call_graph_store", "CallGraphStore", "root_graph"),
        ("artmach_assistant.core.dependency_index_store", "DependencyIndexStore", "root_graph"),
        ("artmach_assistant.core.project_index_store", "ProjectIndexStore", "index"),
    ],
)
def test_remove_uses_same_normalized_root_key(tmp_path, monkeypatch, module_name, class_name, save_kind):
    _install_stubs(tmp_path)
    monkeypatch.chdir(tmp_path)
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    store = getattr(module, class_name)(tmp_path / "cache")

    relative_root = Path("workspace")
    relative_root.mkdir()
    if save_kind == "index":
        index_type = sys.modules["artmach_assistant.core.project_index"].ProjectIndex
        saved = store.save(index_type(relative_root))
    else:
        saved = store.save(relative_root, {})

    assert saved.is_file()
    store.remove(relative_root)
    assert not saved.exists()
