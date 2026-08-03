from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "intent_router.py"
spec = importlib.util.spec_from_file_location("intent_router", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_output_routing_phrases_are_local_commands() -> None:
    router = module.IntentRouter()
    assert router.classify("Jarvis sesi dışarı ver").kind is module.IntentKind.LOCAL_COMMAND
    assert router.classify("sesi içe al").kind is module.IntentKind.LOCAL_COMMAND
