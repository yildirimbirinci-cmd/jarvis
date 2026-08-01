import importlib.util
import sys
import types
from pathlib import Path


class BrokenRegistry:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError("status unavailable")
        return fail


def load_module():
    pkg = types.ModuleType("artmach_assistant")
    core = types.ModuleType("artmach_assistant.core")
    status = types.ModuleType("artmach_assistant.core.service_status")
    status.service_status_registry = BrokenRegistry()
    sys.modules.update({"artmach_assistant": pkg, "artmach_assistant.core": core, "artmach_assistant.core.service_status": status})
    path = Path(__file__).parents[1] / "core" / "index_consistency.py"
    spec = importlib.util.spec_from_file_location("index_consistency_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_status_failures_do_not_break_reconcile():
    module = load_module()
    service = module.IndexConsistencyService(lambda: 3)
    assert service.run_once() == 3
