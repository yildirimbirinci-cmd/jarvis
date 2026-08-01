import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_service_status_clears_transient_recovery_details():
    module = load_module("service_status_under_test", ROOT / "core/service_status.py")
    registry = module.ServiceStatusRegistry()
    registry.set_state("queue", "error", recovering_service="queue", retry_delay_seconds=8)
    registry.failed("queue", RuntimeError("boom"), 0)
    registry.recovered("queue", "ok")
    snapshot = registry.snapshot("queue")
    assert snapshot["last_error"] == ""
    assert snapshot["details"]["recovery_count"] == 1
    assert "recovering_service" not in snapshot["details"]
    assert "retry_delay_seconds" not in snapshot["details"]
    assert "error_type" not in snapshot["details"]


def test_agent_registry_rejects_invalid_risk_and_duplicate_ids(tmp_path, monkeypatch):
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

    rows = [
        {"agent_id": "A1", "name": "Good", "purpose": "Test", "capabilities": ["x"], "allowed_tools": [], "max_risk": "low"},
        {"agent_id": "A2", "name": "Bad", "purpose": "Test", "capabilities": ["x"], "allowed_tools": [], "max_risk": "root"},
        {"agent_id": "A1", "name": "Overwrite", "purpose": "Test", "capabilities": ["y"], "allowed_tools": [], "max_risk": "high"},
        {"agent_id": "A3", "name": "No caps", "purpose": "Test", "capabilities": [], "allowed_tools": [], "max_risk": "low"},
    ]
    (tmp_path / "agent_registry.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    module = load_module("agent_manager_under_test", ROOT / "core/agent_manager.py")
    manager = module.AgentManager(object())
    assert [agent.agent_id for agent in manager.list_agents()] == ["A1"]
    assert manager.list_agents()[0].name == "Good"


def test_failed_registration_rolls_back_memory_state(tmp_path, monkeypatch):
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    config = types.ModuleType("artmach_assistant.config")
    config.DATA_DIR = tmp_path
    constitution = types.ModuleType("artmach_assistant.core.constitution")

    class AgentPolicy:
        def __init__(self, _context):
            self.rules = {"risk_levels": ["low"]}
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
    module = load_module("agent_manager_rollback_test", ROOT / "core/agent_manager.py")
    manager = module.AgentManager(object())
    monkeypatch.setattr(manager, "_append", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    try:
        manager.register(name="Agent", purpose="Test", capabilities=["x"], allowed_tools=[])
    except OSError:
        pass
    else:
        raise AssertionError("registration should fail")
    assert manager.list_agents() == []
