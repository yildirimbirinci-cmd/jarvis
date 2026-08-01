from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.dependency_reindex_queue import DependencyReindexQueue


def test_submit_accepts_a_single_path(tmp_path: Path) -> None:
    batches: list[tuple[Path, ...]] = []
    queue = DependencyReindexQueue(batches.append, batch_wait_seconds=0.05)

    target = tmp_path / "module.py"
    assert queue.submit(target) == 1
    assert queue.flush(timeout=2.0)
    queue.stop()

    assert batches == [(target.resolve(),)]


def test_submit_accepts_a_single_string_path(tmp_path: Path) -> None:
    batches: list[tuple[Path, ...]] = []
    queue = DependencyReindexQueue(batches.append, batch_wait_seconds=0.05)

    target = tmp_path / "module.py"
    assert queue.submit(str(target)) == 1
    assert queue.flush(timeout=2.0)
    queue.stop()

    assert batches == [(target.resolve(),)]


def test_non_finite_wait_values_fall_back_to_safe_defaults(tmp_path: Path) -> None:
    batches: list[tuple[Path, ...]] = []
    queue = DependencyReindexQueue(batches.append, batch_wait_seconds=float("inf"))

    target = tmp_path / "module.py"
    assert queue.submit([target]) == 1
    assert queue.flush(timeout=float("nan"))
    queue.stop()

    assert batches == [(target.resolve(),)]


def test_invalid_non_iterable_submission_is_ignored() -> None:
    queue = DependencyReindexQueue(lambda _batch: None)

    assert queue.submit(123) == 0  # type: ignore[arg-type]
    queue.stop(drain=False)
