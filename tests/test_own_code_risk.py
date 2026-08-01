from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_risk import assess_own_code_proposal


def _change(path: str, old: str, new: str):
    return SimpleNamespace(path=path, old_content=old, new_content=new)


def test_small_local_change_is_low_risk() -> None:
    proposal = SimpleNamespace(
        files=[_change("core/helper.py", "x = 1\n", "x = 2\n")]
    )

    risk = assess_own_code_proposal(proposal)

    assert risk.level == "low"
    assert risk.changed_files == 1
    assert "düşük" in risk.report()


def test_dynamic_execution_in_runtime_core_is_critical() -> None:
    proposal = SimpleNamespace(files=[
        _change(
            "core/assistant.py",
            "def run():\n    return 1\n",
            "def run(value):\n    return eval(value)\n",
        ),
        _change("app.py", "x = 1\n", "x = 2\n"),
    ])

    risk = assess_own_code_proposal(proposal)

    assert risk.level == "critical"
    assert risk.requires_explicit_critical_approval
    assert "dinamik kod" in risk.report()


def test_network_access_expansion_is_critical() -> None:
    proposal = SimpleNamespace(files=[
        _change("core/helper.py", "", "import requests\nrequests.get('https://example.com')\n")
    ])

    risk = assess_own_code_proposal(proposal)

    assert risk.level == "critical"
    assert "güvenlik yetkisi" in risk.report()
