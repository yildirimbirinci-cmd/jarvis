import importlib.util
import json
import sys
import types
from pathlib import Path


def _load_agent_manager(tmp_path: Path, monkeypatch):
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    config = types.ModuleType("artmach_assistant.config")
    config.DATA_DIR = tmp_path
    constitution = types.ModuleType("artmach_assistant.core.constitution")

    class AgentPolicy:
        def __init__(self, _context):
            self.rules = {"risk_levels": ["low", "medium", "high"]}

        def require(self, *_args, **_kwargs):
            return None

        def validate_depth(self, _depth):
            return None

    constitution.AgentPolicy = AgentPolicy
    constitution.ModuleConstitutionContext = object
    monkeypatch.setitem(sys.modules, "artmach_assistant", package)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core", core)
    monkeypatch.setitem(sys.modules, "artmach_assistant.config", config)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core.constitution", constitution)

    path = Path(__file__).resolve().parents[1] / "core" / "agent_manager.py"
    spec = importlib.util.spec_from_file_location("agent_manager_normalization_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_loaded_registry_fields_are_normalized(tmp_path, monkeypatch):
    row = {
        "agent_id": "  AGENT-1  ",
        "name": "  Build Agent  ",
        "purpose": "  Build project indexes  ",
        "capabilities": [" index ", "index", "review"],
        "allowed_tools": [" filesystem ", "filesystem"],
        "max_risk": "low",
    }
    (tmp_path / "agent_registry.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    module = _load_agent_manager(tmp_path, monkeypatch)

    manager = module.AgentManager(object())
    agents = manager.list_agents()

    assert agents == [
        module.AgentDefinition(
            agent_id="AGENT-1",
            name="Build Agent",
            purpose="Build project indexes",
            capabilities=("index", "review"),
            allowed_tools=("filesystem",),
            max_risk="low",
        )
    ]
    assert manager._require_agent("AGENT-1") is agents[0]


def test_duplicate_ids_are_compared_after_normalization(tmp_path, monkeypatch):
    rows = [
        {
            "agent_id": " AGENT-1 ",
            "name": "First",
            "purpose": "First definition",
            "capabilities": ["index"],
            "allowed_tools": [],
            "max_risk": "low",
        },
        {
            "agent_id": "AGENT-1",
            "name": "Second",
            "purpose": "Must not replace first",
            "capabilities": ["review"],
            "allowed_tools": [],
            "max_risk": "high",
        },
    ]
    (tmp_path / "agent_registry.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    module = _load_agent_manager(tmp_path, monkeypatch)

    manager = module.AgentManager(object())

    assert [(agent.agent_id, agent.name) for agent in manager.list_agents()] == [
        ("AGENT-1", "First")
    ]


def test_failed_registration_rolls_back_memory_state(tmp_path, monkeypatch):
    module = _load_agent_manager(tmp_path, monkeypatch)
    manager = module.AgentManager(object())

    def fail_append(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(manager, "_append", fail_append)

    try:
        manager.register(
            name="Agent",
            purpose="Test",
            capabilities=["index"],
            allowed_tools=[],
        )
    except OSError:
        pass
    else:
        raise AssertionError("registration should fail")

    assert manager.list_agents() == []
