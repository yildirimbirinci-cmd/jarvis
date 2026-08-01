import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT if (ROOT / "core").exists() else ROOT / "artmach_assistant"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_learning_module(tmp_path, monkeypatch):
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    config = types.ModuleType("artmach_assistant.config")
    config.DATA_DIR = tmp_path
    router = types.ModuleType("artmach_assistant.core.local_command_router")
    router.normalize_text = lambda value: " ".join(value.casefold().split())
    router.phrase_score = lambda left, right: 1.0 if router.normalize_text(left) == router.normalize_text(right) else 0.0
    monkeypatch.setitem(sys.modules, "artmach_assistant", package)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core", core)
    monkeypatch.setitem(sys.modules, "artmach_assistant.config", config)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core.local_command_router", router)
    return load_module("learning_memory_transaction_test", SOURCE_ROOT / "core/learning_memory.py")


def load_memory_manager_module(tmp_path, monkeypatch):
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
    return load_module("memory_manager_integrity_test", SOURCE_ROOT / "core/memory_manager.py")


def test_teach_rolls_back_in_memory_records_when_save_fails(tmp_path, monkeypatch):
    module = load_learning_module(tmp_path, monkeypatch)
    memory = module.LearningMemory(tmp_path / "learned.json")
    original = module.LearnedMemory(kind="reply", trigger="hello", response="hi")
    memory.records = [original]
    monkeypatch.setattr(memory, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError):
        memory.teach("reply", "new trigger", response="new response")

    assert memory.records == [original]


def test_forget_and_match_roll_back_when_persistence_fails(tmp_path, monkeypatch):
    module = load_learning_module(tmp_path, monkeypatch)
    memory = module.LearningMemory(tmp_path / "learned.json")
    record = module.LearnedMemory(kind="reply", trigger="hello", response="hi", uses=4)
    memory.records = [record]
    monkeypatch.setattr(memory, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError):
        memory.forget("hello")
    assert memory.records == [record]

    with pytest.raises(OSError):
        memory.match("hello")
    assert record.uses == 4


def test_memory_list_tolerates_temporary_read_failure(tmp_path, monkeypatch):
    module = load_memory_manager_module(tmp_path, monkeypatch)
    manager = module.MemoryManager(object())
    manager.path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked")))

    assert manager.list() == []
