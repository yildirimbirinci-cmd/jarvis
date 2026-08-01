from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.constitution.agent_policy import AgentPolicy
from artmach_assistant.core.constitution.exceptions import ConstitutionLoadError
from artmach_assistant.core.constitution.memory_policy import MemoryPolicy
from artmach_assistant.core.constitution.planning_policy import PlanningPolicy
from artmach_assistant.core.constitution.runtime_policy import RuntimePolicy


POLICY_LOADERS = (
    (AgentPolicy, "rules_file", "_load_and_validate"),
    (MemoryPolicy, "rules_file", "_load_and_validate"),
    (PlanningPolicy, "rules_file", "_load_and_validate"),
    (RuntimePolicy, "rules_path", "_load_rules"),
)


def _load(policy_type: type, path_attribute: str, method_name: str, path: Path) -> None:
    policy = object.__new__(policy_type)
    setattr(policy, path_attribute, path)
    getattr(policy, method_name)()


@pytest.mark.parametrize(("policy_type", "path_attribute", "method_name"), POLICY_LOADERS)
def test_policy_rejects_duplicate_json_keys(
    tmp_path: Path,
    policy_type: type,
    path_attribute: str,
    method_name: str,
) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text('{"schema_version": 1, "schema_version": 2}', encoding="utf-8")

    with pytest.raises(ConstitutionLoadError, match="okunamad|okunamadi"):
        _load(policy_type, path_attribute, method_name, rules)


@pytest.mark.parametrize(("policy_type", "path_attribute", "method_name"), POLICY_LOADERS)
def test_policy_rejects_non_finite_numbers(
    tmp_path: Path,
    policy_type: type,
    path_attribute: str,
    method_name: str,
) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text('{"schema_version": NaN}', encoding="utf-8")

    with pytest.raises(ConstitutionLoadError, match="okunamad|okunamadi"):
        _load(policy_type, path_attribute, method_name, rules)


@pytest.mark.parametrize(("policy_type", "path_attribute", "method_name"), POLICY_LOADERS)
def test_policy_rejects_oversized_files(
    tmp_path: Path,
    policy_type: type,
    path_attribute: str,
    method_name: str,
) -> None:
    rules = tmp_path / "rules.json"
    rules.write_bytes(b"{" + (b" " * 1_048_576) + b"}")

    with pytest.raises(ConstitutionLoadError, match="okunamad|okunamadi"):
        _load(policy_type, path_attribute, method_name, rules)
