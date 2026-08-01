import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT if (ROOT / "core").exists() else ROOT / "artmach_assistant"


def load_module(tmp_path, monkeypatch):
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    config = types.ModuleType("artmach_assistant.config")
    config.DATA_DIR = tmp_path
    constitution = types.ModuleType("artmach_assistant.core.constitution")

    class MemoryPolicy:
        def __init__(self, _context):
            pass

        def require(self, *_args, **_kwargs):
            return None

        def layer(self, _layer):
            return {"persistent": True}

        def validate_record(self, **_kwargs):
            return None

    constitution.MemoryPolicy = MemoryPolicy
    constitution.ModuleConstitutionContext = object
    monkeypatch.setitem(sys.modules, "artmach_assistant", package)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core", core)
    monkeypatch.setitem(sys.modules, "artmach_assistant.config", config)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core.constitution", constitution)

    name = f"memory_manager_json_integrity_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(name, SOURCE_ROOT / "core/memory_manager.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def record(**overrides):
    payload = {
        "created_at": "2026-07-29T18:00:00+00:00",
        "workspace": "demo",
        "category": "general",
        "title": "Valid",
        "content": "safe content",
        "memory_id": "MEM-20260729-ABCDEF1234",
        "layer": "project",
        "record_type": "note",
        "verification_state": "verified",
        "source": "user",
        "supersedes": "",
    }
    payload.update(overrides)
    return payload


def test_list_rejects_duplicate_keys_and_keeps_later_valid_record(tmp_path, monkeypatch):
    module = load_module(tmp_path, monkeypatch)
    manager = module.MemoryManager(object())
    invalid = json.dumps(record(title="first"), ensure_ascii=False)[:-1] + ',"title":"second"}'
    valid = json.dumps(record(title="kept"), ensure_ascii=False)
    manager.path.write_text(invalid + "\n" + valid + "\n", encoding="utf-8")

    items = manager.list()

    assert [item.title for item in items] == ["kept"]


def test_list_rejects_non_finite_numbers(tmp_path, monkeypatch):
    module = load_module(tmp_path, monkeypatch)
    manager = module.MemoryManager(object())
    invalid = json.dumps(record(), ensure_ascii=False)[:-1] + ',"score":NaN}'
    manager.path.write_text(invalid + "\n", encoding="utf-8")

    assert manager.list() == []


def test_list_rejects_invalid_utf8_and_keeps_following_record(tmp_path, monkeypatch):
    module = load_module(tmp_path, monkeypatch)
    manager = module.MemoryManager(object())
    valid = json.dumps(record(title="after"), ensure_ascii=False).encode("utf-8")
    manager.path.write_bytes(b'{"title":"bad\xff"}\n' + valid + b"\n")

    assert [item.title for item in manager.list()] == ["after"]


def test_list_skips_oversized_line_and_keeps_following_record(tmp_path, monkeypatch):
    module = load_module(tmp_path, monkeypatch)
    manager = module.MemoryManager(object())
    oversized = b'{' + b'x' * (module._MAX_MEMORY_RECORD_BYTES + 20) + b'}\n'
    valid = json.dumps(record(title="after-large"), ensure_ascii=False).encode("utf-8")
    manager.path.write_bytes(oversized + valid + b"\n")

    assert [item.title for item in manager.list()] == ["after-large"]


def test_list_rejects_non_object_json_root(tmp_path, monkeypatch):
    module = load_module(tmp_path, monkeypatch)
    manager = module.MemoryManager(object())
    manager.path.write_text("[]\n", encoding="utf-8")

    assert manager.list() == []
