from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "indexing" / "call_graph" / "model.py"
spec = importlib.util.spec_from_file_location("pkg38_call_graph_model", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)

CallGraphBuildResult = module.CallGraphBuildResult
CallSite = module.CallSite


class BrokenIterable:
    def __iter__(self):
        raise RuntimeError("broken iterator")


class BrokenText:
    def __str__(self):
        raise RuntimeError("broken text")


def test_none_collections_are_normalized():
    result = CallGraphBuildResult(path="a.py", call_sites=None, edges=None)
    assert result.call_sites == ()
    assert result.edges == ()

def test_broken_iterables_do_not_escape():
    result = CallGraphBuildResult(path="a.py", call_sites=BrokenIterable(), edges=BrokenIterable())
    assert result.call_sites == ()
    assert result.edges == ()

def test_valid_records_are_kept_and_invalid_ones_filtered():
    call = CallSite("a.py", 1, 0, "f()", None, None)
    result = CallGraphBuildResult(path="a.py", call_sites=[object(), call], edges=[])
    assert result.call_sites == (call,)

def test_text_fields_are_safely_normalized():
    result = CallGraphBuildResult(path=BrokenText(), call_sites=(), edges=(), parse_error="  failed  ")
    assert result.path == ""
    assert result.parse_error == "failed"
