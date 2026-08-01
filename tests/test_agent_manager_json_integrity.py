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
    spec = importlib.util.spec_from_file_location("agent_manager_integrity_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _valid_row(agent_id="AGENT-1"):
    return {
        "agent_id": agent_id,
        "name": "Build Agent",
        "purpose": "Build project indexes",
        "capabilities": ["index"],
        "allowed_tools": ["filesystem"],
        "max_risk": "low",
    }


def test_duplicate_json_keys_are_rejected(tmp_path, monkeypatch):
    valid = json.dumps(_valid_row("AGENT-2"))
    duplicate = (
        '{"agent_id":"AGENT-1","agent_id":"ATTACKER",'
        '"name":"Bad","purpose":"Bad","capabilities":["x"],'
        '"allowed_tools":[],"max_risk":"low"}'
    )
    (tmp_path / "agent_registry.jsonl").write_text(duplicate + "\n" + valid + "\n")
    module = _load_agent_manager(tmp_path, monkeypatch)

    manager = module.AgentManager(object())

    assert [agent.agent_id for agent in manager.list_agents()] == ["AGENT-2"]


def test_non_finite_numbers_are_rejected(tmp_path, monkeypatch):
    invalid = json.dumps(_valid_row()).replace('"max_risk": "low"', '"extra": NaN')
    valid = json.dumps(_valid_row("AGENT-2"))
    (tmp_path / "agent_registry.jsonl").write_text(invalid + "\n" + valid + "\n")
    module = _load_agent_manager(tmp_path, monkeypatch)

    manager = module.AgentManager(object())

    assert [agent.agent_id for agent in manager.list_agents()] == ["AGENT-2"]


def test_invalid_utf8_row_is_skipped_without_hiding_later_rows(tmp_path, monkeypatch):
    path = tmp_path / "agent_registry.jsonl"
    path.write_bytes(b'\xff\xfe\n' + json.dumps(_valid_row("AGENT-2")).encode() + b"\n")
    module = _load_agent_manager(tmp_path, monkeypatch)

    manager = module.AgentManager(object())

    assert [agent.agent_id for agent in manager.list_agents()] == ["AGENT-2"]


def test_oversized_row_is_skipped_and_following_row_loads(tmp_path, monkeypatch):
    module = _load_agent_manager(tmp_path, monkeypatch)
    oversized = b'{"padding":"' + (b"x" * (module._MAX_REGISTRY_ROW_BYTES + 50)) + b'"}\n'
    path = tmp_path / "agent_registry.jsonl"
    path.write_bytes(oversized + json.dumps(_valid_row("AGENT-2")).encode() + b"\n")

    manager = module.AgentManager(object())

    assert [agent.agent_id for agent in manager.list_agents()] == ["AGENT-2"]


def test_non_object_json_row_is_rejected(tmp_path, monkeypatch):
    valid = json.dumps(_valid_row("AGENT-2"))
    (tmp_path / "agent_registry.jsonl").write_text("[]\n" + valid + "\n")
    module = _load_agent_manager(tmp_path, monkeypatch)

    manager = module.AgentManager(object())

    assert [agent.agent_id for agent in manager.list_agents()] == ["AGENT-2"]
