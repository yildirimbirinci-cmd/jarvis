from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

PACKAGE_DIR = Path(__file__).resolve().parent
PAYLOAD_DIR = PACKAGE_DIR / "payload"
FILES = (
    Path("core/research_journal_closeout.py"),
    Path("tests/test_research_journal_closeout_phase1.py"),
)
BASELINE_PASSED = 1502
BASELINE_SKIPPED = 7


def _find_root() -> Path:
    candidates = [Path.cwd()]
    home = Path.home()
    candidates.extend(
        [
            home / "Desktop" / "jarvis",
            home / "Desktop" / "artmach_assistant",
        ]
    )
    for candidate in candidates:
        if (candidate / "core").is_dir() and (candidate / "tests").is_dir():
            return candidate.resolve()
    raise SystemExit("PROJE KOKU BULUNAMADI. Scripti jarvis/artmach_assistant klasorunde calistirin.")


def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )


def _pytest_counts(output: str) -> dict[str, int]:
    import re

    result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for key in result:
        match = re.search(rf"(\d+)\s+{key}", output)
        if match:
            result[key] = int(match.group(1))
    return result


def main() -> int:
    root = _find_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = root / ".jarvis_fix_backup" / f"research_journal_closeout_phase1_{stamp}"
    changed: list[str] = []
    created: list[str] = []

    try:
        for relative in FILES:
            source = PAYLOAD_DIR / relative
            target = root / relative
            if not source.is_file():
                raise RuntimeError(f"PAKET DOSYASI YOK: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = backup_dir / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                changed.append(relative.as_posix())
            else:
                created.append(relative.as_posix())
            shutil.copy2(source, target)

        compile_result = _run(
            root,
            [sys.executable, "-m", "py_compile", str(root / FILES[0]), str(root / FILES[1])],
        )
        if compile_result.returncode:
            raise RuntimeError("DERLEME BASARISIZ\n" + compile_result.stdout)

        focused = _run(
            root,
            [sys.executable, "-m", "pytest", "-q", FILES[1].as_posix()],
        )
        if focused.returncode:
            raise RuntimeError("ODAK TEST BASARISIZ\n" + focused.stdout)

        full = _run(root, [sys.executable, "-m", "pytest", "-q"])
        full_counts = _pytest_counts(full.stdout)
        if full.returncode or full_counts["failed"] or full_counts["errors"]:
            raise RuntimeError("TAM TEST BASARISIZ\n" + full.stdout)
        if full_counts["passed"] < BASELINE_PASSED:
            raise RuntimeError(
                f"BASELINE REGRESYONU: passed={full_counts['passed']} < {BASELINE_PASSED}"
            )

        report = {
            "phase": "Research Journal Closeout Phase 1",
            "project_root": str(root),
            "installed_files": [item.as_posix() for item in FILES],
            "backup_dir": str(backup_dir) if backup_dir.exists() else "",
            "compile": {"ok": True, "output": compile_result.stdout},
            "focused_tests": {
                "ok": True,
                "counts": _pytest_counts(focused.stdout),
                "output": focused.stdout,
            },
            "full_tests": {
                "ok": True,
                "counts": full_counts,
                "output": full.stdout,
            },
            "baseline": {"passed": BASELINE_PASSED, "skipped": BASELINE_SKIPPED},
            "research_engine_modified": False,
            "status": "COMPLETE",
        }
        report_path = root.parent / "research_journal_closeout_phase1_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("RESEARCH JOURNAL CLOSEOUT PHASE 1 KURULDU")
        print(f"ODAK TEST: {focused.stdout.strip()}")
        print(f"TAM TEST: {full.stdout.strip().splitlines()[-1] if full.stdout.strip() else 'OK'}")
        print(f"RAPOR: {report_path}")
        return 0
    except Exception as exc:
        for relative in FILES:
            target = root / relative
            backup = backup_dir / relative
            if backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
            elif relative.as_posix() in created:
                target.unlink(missing_ok=True)
        print(str(exc))
        print("KURULUM GERI ALINDI")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
