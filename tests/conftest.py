from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parent

# Support both import styles used by the project and historical tests:
#   import indexing
#   import artmach_assistant.indexing
for path in (REPOSITORY_ROOT, PACKAGE_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)


# Historical package aliases used by staged SAE 8.3 test files.
_LEGACY_PACKAGE_ALIASES = (
    "pkg16test",
    "pkg18work",
    "pkg19work",
    "pkg39work",
    "pkg40work",
    "pkg41work",
    "pkg42work",
    "pkg43work",
    "pkg44work",
    "pkg45work",
    "pkg46verified",
    "pkg52",
)

for alias in _LEGACY_PACKAGE_ALIASES:
    if alias not in sys.modules:
        module = types.ModuleType(alias)
        module.__path__ = [str(PACKAGE_ROOT)]
        module.__package__ = alias
        sys.modules[alias] = module


class StatusRegistry:
    """Small status stub used by isolated service-runtime tests."""

    def __init__(self) -> None:
        self.calls = []
        self.raise_all = False

    def __getattr__(self, name):
        def call(*args, **kwargs):
            if self.raise_all:
                raise RuntimeError("status unavailable")
            self.calls.append((name, args, kwargs))

        return call


STATUS = StatusRegistry()


def load_module(name: str, relative: str):
    """Load one source module with the minimal legacy dependency stubs."""

    status_mod = types.ModuleType("artmach_assistant.core.service_status")
    status_mod.service_status_registry = STATUS
    sys.modules[status_mod.__name__] = status_mod

    project_mod = types.ModuleType("artmach_assistant.core.project_index")
    project_mod.IGNORED_DIRS = {".git", "__pycache__", ".venv"}
    sys.modules[project_mod.__name__] = project_mod

    spec = importlib.util.spec_from_file_location(name, PACKAGE_ROOT / relative)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load test module: {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Some historical tests install temporary dependency stubs under real project
# module names.  Pytest imports every test module during collection, so those
# stubs must not leak into later modules and make the suite order-dependent.
_MODULE_SNAPSHOTS: dict[str, dict[str, object]] = {}
_PACKAGE_PREFIXES = ("artmach_assistant", "core", "indexing")


def _is_project_module(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in _PACKAGE_PREFIXES)


def _snapshot_project_modules() -> dict[str, object]:
    return {name: module for name, module in sys.modules.items() if _is_project_module(name)}


def _restore_parent_bindings() -> None:
    for name, module in tuple(sys.modules.items()):
        if not _is_project_module(name) or "." not in name:
            continue
        parent_name, child_name = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            try:
                setattr(parent, child_name, module)
            except (AttributeError, TypeError):
                pass


def _restore_project_modules(snapshot: dict[str, object]) -> None:
    for name in tuple(sys.modules):
        if _is_project_module(name) and name not in snapshot:
            sys.modules.pop(name, None)
    for name, module in snapshot.items():
        sys.modules[name] = module
    _restore_parent_bindings()
    importlib.invalidate_caches()


def pytest_collectstart(collector) -> None:
    if collector.__class__.__name__ != "Module":
        return
    nodeid = getattr(collector, "nodeid", str(getattr(collector, "path", "")))
    _MODULE_SNAPSHOTS[nodeid] = _snapshot_project_modules()


def pytest_collectreport(report) -> None:
    snapshot = _MODULE_SNAPSHOTS.pop(report.nodeid, None)
    if snapshot is not None:
        _restore_project_modules(snapshot)


@pytest.fixture(autouse=True)
def _isolate_project_module_cache():
    """Rollback sys.modules mutations performed by an individual test."""

    snapshot = _snapshot_project_modules()
    yield
    _restore_project_modules(snapshot)


@pytest.fixture
def node(tmp_path):
    from indexing.semantic_graph_builder import SemanticNode

    source = tmp_path / "project" / "a.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text("value = 1\n", encoding="utf-8")
    return SemanticNode("node:a", "global_variable", "value", "value", str(source), 1, 1)


@pytest.fixture
def db(tmp_path):
    from indexing.semantic_graph_database import SemanticGraphDatabase

    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return SemanticGraphDatabase(project, directory=tmp_path / "semantic-storage")


@pytest.fixture
def graph(tmp_path):
    from indexing.semantic_graph import SemanticGraph

    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return SemanticGraph(project)
