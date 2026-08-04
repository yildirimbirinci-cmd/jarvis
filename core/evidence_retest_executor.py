from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
)


RETEST_PASSED = "PASSED"
RETEST_FAILED = "FAILED"
RETEST_BLOCKED = "BLOCKED"

_MAX_PRIMARY_TESTS = 3
_OUTPUT_TAIL_LIMIT = 12000


@dataclass(frozen=True, slots=True)
class RetestExecutionResult:
    status: str
    title: str
    path: str
    symbol: str
    command: tuple[str, ...] = ()
    returncode: int | None = None
    duration_ms: float = 0.0
    stdout_tail: str = ""
    stderr_tail: str = ""
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == RETEST_PASSED


def _safe_test_argument(value: str) -> bool:
    normalized = str(value or "").replace("\\", "/").strip()

    if not normalized:
        return False

    path_part = normalized.split("::", 1)[0]

    if (
        not path_part.startswith("tests/")
        or not path_part.endswith(".py")
        or Path(path_part).is_absolute()
        or ".." in Path(path_part).parts
    ):
        return False

    return True


def validate_primary_retest_command(
    item: RetestItem,
) -> tuple[bool, str]:
    if item.status != AUTOMATED:
        return False, "Bulgu otomatik yeniden teste uygun degil."

    command = tuple(item.command)

    if len(command) < 5:
        return False, "Primary pytest komutu eksik."

    if command[:3] != ("python", "-m", "pytest"):
        return False, "Yalnizca python -m pytest komutu kabul edilir."

    if command[-1] != "-q":
        return False, "Pytest komutu -q ile bitmelidir."

    test_arguments = command[3:-1]

    if not test_arguments:
        return False, "Primary test listesi bos."

    if len(test_arguments) > _MAX_PRIMARY_TESTS:
        return False, "Primary test sayisi izin verilen siniri asiyor."

    if not all(
        _safe_test_argument(argument)
        for argument in test_arguments
    ):
        return False, "Guvenli olmayan test yolu bulundu."

    if tuple(test_arguments) != tuple(item.primary_test_paths):
        return False, "Komut ile primary test listesi uyusmuyor."

    return True, ""


def execute_primary_retest(
    item: RetestItem,
    *,
    source_root: str | Path,
    timeout_seconds: float = 300.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RetestExecutionResult:
    valid, reason = validate_primary_retest_command(item)

    if not valid:
        return RetestExecutionResult(
            status=RETEST_BLOCKED,
            title=item.title,
            path=item.path,
            symbol=item.symbol,
            reason=reason,
        )

    root = Path(source_root).resolve(strict=False)

    command = (
        sys.executable,
        "-m",
        "pytest",
        *item.primary_test_paths,
        "-q",
    )

    started = time.perf_counter()

    try:
        completed = runner(
            list(command),
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = (
            time.perf_counter() - started
        ) * 1000.0

        return RetestExecutionResult(
            status=RETEST_BLOCKED,
            title=item.title,
            path=item.path,
            symbol=item.symbol,
            command=command,
            duration_ms=duration_ms,
            stdout_tail=str(exc.stdout or "")[-_OUTPUT_TAIL_LIMIT:],
            stderr_tail=str(exc.stderr or "")[-_OUTPUT_TAIL_LIMIT:],
            reason="Primary yeniden test zaman asimina ugradi.",
        )
    except OSError as exc:
        duration_ms = (
            time.perf_counter() - started
        ) * 1000.0

        return RetestExecutionResult(
            status=RETEST_BLOCKED,
            title=item.title,
            path=item.path,
            symbol=item.symbol,
            command=command,
            duration_ms=duration_ms,
            reason=(
                "Pytest sureci baslatilamadi: "
                f"{type(exc).__name__}: {exc}"
            ),
        )

    duration_ms = (
        time.perf_counter() - started
    ) * 1000.0

    status = (
        RETEST_PASSED
        if completed.returncode == 0
        else RETEST_FAILED
    )

    return RetestExecutionResult(
        status=status,
        title=item.title,
        path=item.path,
        symbol=item.symbol,
        command=command,
        returncode=completed.returncode,
        duration_ms=duration_ms,
        stdout_tail=str(completed.stdout or "")[-_OUTPUT_TAIL_LIMIT:],
        stderr_tail=str(completed.stderr or "")[-_OUTPUT_TAIL_LIMIT:],
        reason=(
            "Primary yeniden testler gecti."
            if status == RETEST_PASSED
            else "Primary yeniden testlerden en az biri basarisiz oldu."
        ),
    )
