"""Preflight gate for Jarvis' guarded self-development handoff.

The gate is intentionally read-only. It verifies that the repository is clean,
Ollama is reachable with at least one model, the guarded own-code modules are
present, and the focused safety suite passes before Jarvis is allowed to apply
its first autonomous patch.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable
from urllib.request import urlopen


SAFETY_TESTS = (
    "tests/test_own_code_readiness.py",
    "tests/test_own_code_closed_loop.py",
    "tests/test_own_code_repair_retry.py",
    "tests/test_own_code_scope.py",
    "tests/test_own_code_dependency_guard.py",
    "tests/test_own_code_security_guard.py",
    "tests/test_own_code_semantic_guard.py",
    "tests/test_own_code_symbol_guard.py",
    "tests/test_self_repair_state_machine.py",
    "tests/test_git_workspace_service.py",
    "tests/test_self_development_cli.py",
    "tests/test_self_development_handoff.py",
)

REQUIRED_FILES = (
    "core/self_development_cli.py",
    "core/own_code_readiness.py",
    "core/own_code_approval.py",
    "core/own_code_authority.py",
    "core/own_code_security_guard.py",
    "core/refactoring_transaction_history.py",
)


@dataclass(frozen=True, slots=True)
class GateCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class GateResult:
    ready: bool
    checks: tuple[GateCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "ready": self.ready,
            "checks": [asdict(check) for check in self.checks],
        }

    def report(self) -> str:
        title = "SELF-DEVELOPMENT GATE: READY" if self.ready else "SELF-DEVELOPMENT GATE: BLOCKED"
        rows = [
            f"- {'PASS' if check.ok else 'FAIL'} | {check.name}: {check.detail}"
            for check in self.checks
        ]
        return title + "\n" + "\n".join(rows)


def _git_clean(root: Path) -> GateCheck:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        return GateCheck("git worktree", False, completed.stdout.strip() or "git status failed")
    dirty = [line for line in completed.stdout.splitlines() if line.strip()]
    return GateCheck(
        "git worktree",
        not dirty,
        "clean" if not dirty else f"{len(dirty)} uncommitted path(s)",
    )


def _required_files(root: Path) -> GateCheck:
    missing = [relative for relative in REQUIRED_FILES if not (root / relative).is_file()]
    return GateCheck(
        "guarded modules",
        not missing,
        "present" if not missing else "missing: " + ", ".join(missing),
    )


def _ollama_models(url: str = "http://127.0.0.1:11434/api/tags") -> GateCheck:
    try:
        with urlopen(url, timeout=10) as response:  # noqa: S310 - fixed local endpoint by default
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return GateCheck("ollama", False, f"unreachable: {exc}")
    models = [str(item.get("name", "")).strip() for item in payload.get("models", [])]
    models = [name for name in models if name]
    return GateCheck(
        "ollama",
        bool(models),
        ", ".join(models) if models else "no installed model",
    )


def _safety_suite(root: Path) -> GateCheck:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *(str(root / relative) for relative in SAFETY_TESTS),
    ]
    completed = subprocess.run(
        command,
        cwd=root.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    tail = "\n".join(completed.stdout.splitlines()[-8:]).strip()
    return GateCheck(
        "self-development safety suite",
        completed.returncode == 0,
        tail or f"pytest exit code {completed.returncode}",
    )


def assess_self_development_gate(
    root: Path,
    *,
    git_check: Callable[[Path], GateCheck] = _git_clean,
    file_check: Callable[[Path], GateCheck] = _required_files,
    ollama_check: Callable[[], GateCheck] = _ollama_models,
    test_check: Callable[[Path], GateCheck] = _safety_suite,
) -> GateResult:
    root = Path(root).resolve()
    checks = (
        git_check(root),
        file_check(root),
        ollama_check(),
        test_check(root),
    )
    return GateResult(all(check.ok for check in checks), checks)


def write_gate_report(result: GateResult, path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return target
