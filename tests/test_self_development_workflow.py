from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "self-development.yml"
SCRIPT = ROOT / "scripts" / "run_self_development.ps1"


def test_self_development_workflow_is_manual_and_windows_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "runs-on: [self-hosted, Windows, X64]" in text
    assert "permissions:\n  contents: read" in text


def test_self_development_workflow_does_not_persist_git_credentials() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "persist-credentials: false" in text
    assert "git push" not in text
    assert "actions/upload-artifact@v4" in text
    assert "proposed-change.diff" in text


def test_self_development_workflow_requires_ollama_and_uses_guarded_cli() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "http://127.0.0.1:11434/api/tags" in text
    assert '"--self-develop"' in text
    assert '"--self-develop-stage"' in text
    assert "timeout-minutes: 45" in text


def test_local_powershell_wrapper_uses_same_guarded_cli() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert '[ValidateSet("plan", "propose", "apply")]' in text
    assert "python -m artmach_assistant --self-develop" in text
    assert "--self-develop-stage $Stage" in text


def test_workflow_blocks_apply_until_handoff_gate_passes() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--self-develop-check" in text
    assert "self-development-gate.json" in text
    assert text.index("Verify self-development handoff gate") < text.index("Run guarded self-development")


def test_workflow_exposes_guarded_handoff_stage() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "- handoff" in text
    assert "--self-develop-handoff" in text
    assert "--acknowledge-self-modification" in text
    assert "self-development-handoff.json" in text
