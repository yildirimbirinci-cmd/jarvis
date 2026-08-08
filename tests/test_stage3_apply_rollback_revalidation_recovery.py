from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.core.own_code_apply_recovery import OwnCodeApplyRecoveryStore


@dataclass
class Change:
    path: str
    old_content: str
    new_content: str


@dataclass
class Proposal:
    files: tuple[Change, ...]


def _proposal() -> Proposal:
    return Proposal((Change("core/example.py", "old\n", "new\n"),))


def test_baseline_is_recognized(tmp_path: Path) -> None:
    target = tmp_path / "core" / "example.py"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    store = OwnCodeApplyRecoveryStore(tmp_path / ".jarvis" / "apply.json")
    store.save_prepared(_proposal(), "f" * 64)
    result = store.inspect(tmp_path)
    assert result.disposition == "baseline"


def test_fully_applied_requires_revalidation(tmp_path: Path) -> None:
    target = tmp_path / "core" / "example.py"
    target.parent.mkdir()
    target.write_text("new\n", encoding="utf-8")
    store = OwnCodeApplyRecoveryStore(tmp_path / ".jarvis" / "apply.json")
    store.save_prepared(_proposal(), "f" * 64)
    store.mark_phase("applied")
    result = store.inspect(tmp_path)
    assert result.disposition == "applied"


def test_partial_apply_is_fail_closed(tmp_path: Path) -> None:
    proposal = Proposal((
        Change("core/a.py", "a-old", "a-new"),
        Change("core/b.py", "b-old", "b-new"),
    ))
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("a-new", encoding="utf-8")
    (tmp_path / "core" / "b.py").write_text("b-old", encoding="utf-8")
    store = OwnCodeApplyRecoveryStore(tmp_path / ".jarvis" / "apply.json")
    store.save_prepared(proposal, "a" * 64)
    assert store.inspect(tmp_path).disposition == "partial"


def test_diverged_apply_is_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "core" / "example.py"
    target.parent.mkdir()
    target.write_text("unexpected\n", encoding="utf-8")
    store = OwnCodeApplyRecoveryStore(tmp_path / ".jarvis" / "apply.json")
    store.save_prepared(_proposal(), "f" * 64)
    assert store.inspect(tmp_path).disposition == "diverged"


def test_state_is_atomic_and_clearable(tmp_path: Path) -> None:
    store = OwnCodeApplyRecoveryStore(tmp_path / ".jarvis" / "apply.json")
    store.save_prepared(_proposal(), "f" * 64)
    assert store.load() is not None
    store.mark_phase("validating")
    assert store.load().phase == "validating"
    store.clear()
    assert store.load() is None


def test_crlf_source_matches_lf_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "core" / "example.py"
    target.parent.mkdir()
    target.write_bytes(b"old\r\n")
    store = OwnCodeApplyRecoveryStore(tmp_path / ".jarvis" / "apply.json")
    store.save_prepared(_proposal(), "f" * 64)
    assert store.inspect(tmp_path).disposition == "baseline"


def test_crlf_applied_source_matches_lf_proposal(tmp_path: Path) -> None:
    target = tmp_path / "core" / "example.py"
    target.parent.mkdir()
    target.write_bytes(b"new\r\n")
    store = OwnCodeApplyRecoveryStore(tmp_path / ".jarvis" / "apply.json")
    store.save_prepared(_proposal(), "f" * 64)
    store.mark_phase("applied")
    assert store.inspect(tmp_path).disposition == "applied"
