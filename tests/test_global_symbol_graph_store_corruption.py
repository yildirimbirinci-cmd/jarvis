import importlib.util
import json
import sys
import types
from pathlib import Path


def load_module(tmp_path):
    pkg = types.ModuleType("artmach_assistant")
    core = types.ModuleType("artmach_assistant.core")
    config = types.ModuleType("artmach_assistant.config")
    config.DATA_DIR = tmp_path
    path_mod = types.ModuleType("artmach_assistant.core.path_normalizer")
    path_mod.normalize_project_root = lambda value: Path(value).resolve(strict=False)
    path_mod.path_key = lambda value: str(Path(value).resolve(strict=False)).casefold()
    validation = types.ModuleType("artmach_assistant.core.store_validation")
    validation.atomic_write_json = lambda path, payload, max_bytes: path.write_text(json.dumps(payload), encoding="utf-8")
    validation.read_json_object = lambda path, max_bytes: json.loads(path.read_text(encoding="utf-8"))
    validation.require_schema_version = lambda payload, field, expected: (_ for _ in ()).throw(ValueError()) if payload.get(field) != expected else None
    sys.modules.update({"artmach_assistant": pkg, "artmach_assistant.core": core, "artmach_assistant.config": config, "artmach_assistant.core.path_normalizer": path_mod, "artmach_assistant.core.store_validation": validation})
    source = Path(__file__).parents[1] / "core" / "global_symbol_graph_store.py"
    spec = importlib.util.spec_from_file_location("global_store_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corrupt_snapshot_is_removed(tmp_path):
    module = load_module(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    store = module.GlobalSymbolGraphStore(tmp_path / "store")
    store.directory.mkdir()
    target = store._path_for(root)
    target.write_text("{broken", encoding="utf-8")
    assert store.load(root) is None
    assert not target.exists()
