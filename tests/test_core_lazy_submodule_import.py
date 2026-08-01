from __future__ import annotations

import importlib
import sys

import pytest


def test_core_package_lazily_exposes_existing_submodule() -> None:
    core = importlib.import_module("artmach_assistant.core")
    sys.modules.pop("artmach_assistant.core.skill_registry", None)
    core.__dict__.pop("skill_registry", None)

    module = core.skill_registry

    assert module.__name__ == "artmach_assistant.core.skill_registry"
    assert core.skill_registry is module


def test_core_package_rejects_unknown_attribute() -> None:
    core = importlib.import_module("artmach_assistant.core")

    with pytest.raises(AttributeError):
        getattr(core, "module_that_does_not_exist")
