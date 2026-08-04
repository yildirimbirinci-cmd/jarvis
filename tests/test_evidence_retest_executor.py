from __future__ import annotations

import subprocess
import sys

from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    BLOCKED,
    RetestItem,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_BLOCKED,
    RETEST_FAILED,
    RETEST_PASSED,
    execute_primary_retest,
    validate_primary_retest_command,
)


def _item(
    *,
    status: str = AUTOMATED,
    paths: tuple[str, ...] = (
        "tests/test_example.py",
    ),
) -> RetestItem:
    return RetestItem(
        title="Example.run yeniden testi",
        path="core/example.py",
        symbol="Example.run",
        status=status,
        primary_test_paths=paths,
        test_paths=paths,
        command=(
            "python",
            "-m",
            "pytest",
            *paths,
            "-q",
        ),
    )


def test_valid_primary_command_is_accepted() -> None:
    valid, reason = validate_primary_retest_command(
        _item()
    )

    assert valid is True
    assert reason == ""


def test_non_automated_item_is_blocked(tmp_path) -> None:
    result = execute_primary_retest(
        _item(status=BLOCKED),
        source_root=tmp_path,
    )

    assert result.status == RETEST_BLOCKED
    assert result.returncode is None


def test_unsafe_test_path_is_blocked(tmp_path) -> None:
    item = _item(paths=("../outside.py",))

    result = execute_primary_retest(
        item,
        source_root=tmp_path,
    )

    assert result.status == RETEST_BLOCKED
    assert "Guvenli olmayan" in result.reason


def test_successful_primary_retest_returns_passed(
    tmp_path,
) -> None:
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="1 passed\n",
            stderr="",
        )

    result = execute_primary_retest(
        _item(),
        source_root=tmp_path,
        runner=runner,
    )

    assert result.status == RETEST_PASSED
    assert result.returncode == 0
    assert "1 passed" in result.stdout_tail
    assert calls[0][0][0] == sys.executable
    assert calls[0][1]["cwd"] == str(
        tmp_path.resolve()
    )
    assert calls[0][1]["shell"] is False


def test_failed_primary_retest_returns_failed(
    tmp_path,
) -> None:
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="1 failed\n",
            stderr="assertion failed\n",
        )

    result = execute_primary_retest(
        _item(),
        source_root=tmp_path,
        runner=runner,
    )

    assert result.status == RETEST_FAILED
    assert result.returncode == 1
    assert "1 failed" in result.stdout_tail
    assert "assertion failed" in result.stderr_tail


def test_timeout_is_reported_as_blocked(
    tmp_path,
) -> None:
    def runner(command, **_kwargs):
        raise subprocess.TimeoutExpired(
            command,
            timeout=1,
            output="partial output",
        )

    result = execute_primary_retest(
        _item(),
        source_root=tmp_path,
        runner=runner,
    )

    assert result.status == RETEST_BLOCKED
    assert "zaman asimina" in result.reason
