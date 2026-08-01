import importlib.util
import json
import os
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


def install_agent_manager_stubs(tmp_path: Path, monkeypatch):
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

        def validate_depth(self, depth):
            if depth < 0:
                raise ValueError("invalid depth")

    constitution.AgentPolicy = AgentPolicy
    constitution.ModuleConstitutionContext = object
    monkeypatch.setitem(sys.modules, "artmach_assistant", package)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core", core)
    monkeypatch.setitem(sys.modules, "artmach_assistant.config", config)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core.constitution", constitution)


def test_jsonl_append_rolls_back_partial_row_when_fsync_fails(tmp_path, monkeypatch):
    install_agent_manager_stubs(tmp_path, monkeypatch)
    module = load_module("agent_manager_append_test", ROOT / "core/agent_manager.py")
    path = tmp_path / "audit.jsonl"
    path.write_bytes(b'{"existing":true}\n')
    original = path.read_bytes()

    monkeypatch.setattr(module.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk full")))
    try:
        module.AgentManager._append(path, {"run_id": "RUN-1"})
    except OSError:
        pass
    else:
        raise AssertionError("fsync failure should propagate")

    assert path.read_bytes() == original


def test_jsonl_append_writes_one_valid_compact_record(tmp_path, monkeypatch):
    install_agent_manager_stubs(tmp_path, monkeypatch)
    module = load_module("agent_manager_valid_append_test", ROOT / "core/agent_manager.py")
    path = tmp_path / "audit.jsonl"

    module.AgentManager._append(path, {"name": "Ajan", "tools": ["read"]})

    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0]) == {"name": "Ajan", "tools": ["read"]}


def install_agent_runner_stubs(monkeypatch):
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    analyzer = types.ModuleType("artmach_assistant.core.build_analyzer")

    class Analysis:
        def report(self):
            return "build error"

    class BuildLogAnalyzer:
        def analyze(self, _output):
            return Analysis()

    manager = types.ModuleType("artmach_assistant.core.build_manager")

    class BuildResult:
        pass

    analyzer.BuildLogAnalyzer = BuildLogAnalyzer
    manager.BuildResult = BuildResult
    monkeypatch.setitem(sys.modules, "artmach_assistant", package)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core", core)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core.build_analyzer", analyzer)
    monkeypatch.setitem(sys.modules, "artmach_assistant.core.build_manager", manager)


def test_agent_report_distinguishes_success_failure_and_missing_validation(monkeypatch):
    install_agent_runner_stubs(monkeypatch)
    module = load_module("agent_runner_report_test", ROOT / "core/agent_runner.py")

    no_build = module.AgentRunResult("edit applied", [])
    assert no_build.succeeded is False
    assert no_build.report().startswith("KOD AJANI DOĞRULANAMADI")

    profile = types.SimpleNamespace(name="pytest")
    failed_result = types.SimpleNamespace(profile=profile, succeeded=False, output="boom")
    failed = module.AgentRunResult("edit applied", [failed_result])
    assert failed.succeeded is False
    assert failed.report().startswith("KOD AJANI BAŞARISIZ")
    assert "build error" in failed.report()

    ok_result = types.SimpleNamespace(profile=profile, succeeded=True, output="")
    succeeded = module.AgentRunResult("edit applied", [ok_result])
    assert succeeded.succeeded is True
    assert succeeded.report().startswith("KOD AJANI BAŞARILI")
