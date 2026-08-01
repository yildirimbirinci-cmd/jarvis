from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


def _load_store(tmp_path: Path):
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    sys.modules["artmach_assistant"] = package

    config = types.ModuleType("artmach_assistant.config")
    config.DATA_DIR = tmp_path
    sys.modules[config.__name__] = config

    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    sys.modules[core.__name__] = core

    normalizer = types.ModuleType("artmach_assistant.core.path_normalizer")
    normalizer.normalize_project_root = lambda value: Path(value).resolve(strict=False)
    normalizer.path_key = lambda value: str(Path(value).resolve(strict=False)).casefold()
    sys.modules[normalizer.__name__] = normalizer

    validation = types.ModuleType("artmach_assistant.core.store_validation")

    def atomic_write_json(path, payload, *, max_bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload)
        assert len(text.encode("utf-8")) <= max_bytes
        path.write_text(text, encoding="utf-8")

    def read_json_object(path, *, max_bytes):
        assert path.stat().st_size <= max_bytes
        return json.loads(path.read_text(encoding="utf-8"))

    def require_schema_version(payload, *, field, expected):
        if payload.get(field) != expected:
            raise ValueError("schema mismatch")

    validation.atomic_write_json = atomic_write_json
    validation.read_json_object = read_json_object
    validation.require_schema_version = require_schema_version
    sys.modules[validation.__name__] = validation

    source = Path(__file__).parents[1] / "core" / "dependency_index_store.py"
    spec = importlib.util.spec_from_file_location("dependency_index_store_under_test", source)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.DependencyIndexStore(directory=tmp_path / "indexes")


@pytest.mark.parametrize("bad_path", ["", "   ", "bad\x00path.py"])
def test_save_rejects_invalid_source_paths(tmp_path: Path, bad_path: str) -> None:
    store = _load_store(tmp_path)
    with pytest.raises(ValueError):
        store.save(tmp_path, {bad_path: []})


@pytest.mark.parametrize("bad_path", ["", "   ", "bad\x00path.py"])
def test_save_rejects_invalid_dependency_paths(tmp_path: Path, bad_path: str) -> None:
    store = _load_store(tmp_path)
    with pytest.raises(ValueError):
        store.save(tmp_path, {"source.py": [bad_path]})


def test_valid_graph_round_trip_and_deduplication(tmp_path: Path) -> None:
    store = _load_store(tmp_path)
    store.save(tmp_path, {"source.py": ["pkg/mod.py", "pkg/mod.py"]})
    assert store.load(tmp_path) == {"source.py": ["pkg/mod.py"]}
