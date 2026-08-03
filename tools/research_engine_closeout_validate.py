from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

BASELINE_PASSED = 1373
BASELINE_SKIPPED = 7
FOCUSED_TESTS = (
    "tests/test_self_improvement_research.py",
)


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str


@dataclass
class ValidationReport:
    project_root: str
    python: str
    started_at_epoch: float
    compile_ok: bool
    focused_ok: bool
    full_ok: bool
    passed: int | None
    skipped: int | None
    failed: int | None
    errors: int | None
    baseline_passed: int
    baseline_skipped: int
    baseline_comparison: str
    research_engine_status: str
    commands: list[CommandResult]


def _run(command: Sequence[str], cwd: Path) -> CommandResult:
    started = time.monotonic()
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    return CommandResult(
        command=list(command),
        returncode=completed.returncode,
        duration_seconds=round(time.monotonic() - started, 3),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _parse_pytest_counts(text: str) -> tuple[int | None, int | None, int | None, int | None]:
    def count(label: str) -> int | None:
        matches = re.findall(rf"(\d+)\s+{label}", text)
        return int(matches[-1]) if matches else None

    return count("passed"), count("skipped"), count("failed"), count("error(?:s)?")


def _baseline_comparison(passed: int | None, skipped: int | None, full_ok: bool) -> str:
    if not full_ok:
        return "REGRESSION_OR_TEST_FAILURE"
    if passed is None:
        return "COUNTS_NOT_PARSED"
    if passed < BASELINE_PASSED:
        return f"PASSED_COUNT_BELOW_BASELINE:{passed}<{BASELINE_PASSED}"
    if skipped is not None and skipped > BASELINE_SKIPPED:
        return f"SKIPPED_COUNT_ABOVE_BASELINE:{skipped}>{BASELINE_SKIPPED}"
    if passed == BASELINE_PASSED and (skipped is None or skipped == BASELINE_SKIPPED):
        return "MATCHES_BASELINE"
    return "MEETS_OR_EXCEEDS_BASELINE"


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    commands: list[CommandResult] = []

    required = [
        project_root / "core" / "assistant.py",
        project_root / "core" / "self_improvement_research.py",
        project_root / "tests" / "test_self_improvement_research.py",
    ]
    missing = [str(path.relative_to(project_root)) for path in required if not path.exists()]
    if missing:
        print("Eksik dosyalar: " + ", ".join(missing), file=sys.stderr)
        return 2

    compile_command = [
        sys.executable,
        "-m",
        "py_compile",
        "core/assistant.py",
        "core/self_improvement_research.py",
    ]
    compile_result = _run(compile_command, project_root)
    commands.append(compile_result)

    focused_command = [sys.executable, "-m", "pytest", "-q", *FOCUSED_TESTS]
    focused_result = _run(focused_command, project_root)
    commands.append(focused_result)

    full_command = [sys.executable, "-m", "pytest", "-q"]
    full_result = _run(full_command, project_root)
    commands.append(full_result)

    full_text = full_result.stdout + "\n" + full_result.stderr
    passed, skipped, failed, errors = _parse_pytest_counts(full_text)
    comparison = _baseline_comparison(passed, skipped, full_result.returncode == 0)

    complete = (
        compile_result.returncode == 0
        and focused_result.returncode == 0
        and full_result.returncode == 0
        and comparison in {"MATCHES_BASELINE", "MEETS_OR_EXCEEDS_BASELINE"}
    )

    report = ValidationReport(
        project_root=str(project_root),
        python=sys.executable,
        started_at_epoch=time.time(),
        compile_ok=compile_result.returncode == 0,
        focused_ok=focused_result.returncode == 0,
        full_ok=full_result.returncode == 0,
        passed=passed,
        skipped=skipped,
        failed=failed,
        errors=errors,
        baseline_passed=BASELINE_PASSED,
        baseline_skipped=BASELINE_SKIPPED,
        baseline_comparison=comparison,
        research_engine_status="COMPLETE" if complete else "NOT_COMPLETE",
        commands=commands,
    )

    report_path = project_root / "research_engine_closeout_report.json"
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Research Engine kapanış doğrulaması")
    print(f"Derleme: {'GEÇTİ' if report.compile_ok else 'BAŞARISIZ'}")
    print(f"Odaklı testler: {'GEÇTİ' if report.focused_ok else 'BAŞARISIZ'}")
    print(f"Tam test paketi: {'GEÇTİ' if report.full_ok else 'BAŞARISIZ'}")
    print(f"Sonuç: passed={passed}, skipped={skipped}, failed={failed}, errors={errors}")
    print(f"Baseline karşılaştırması: {comparison}")
    print(f"Research Engine durumu: {report.research_engine_status}")
    print(f"Ayrıntılı rapor: {report_path}")

    if not complete:
        print("\nSon tam test çıktısı:\n")
        print(full_text[-12000:])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
