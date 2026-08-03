from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPORT_NAME = "research_engine_closeout_final_regression_report.json"
LOG_NAME = "research_engine_closeout_final_regression_pytest.log"
FOCUSED_TESTS = (
    "tests/test_self_improvement_research.py",
    "tests/test_notification_store.py",
    "tests/test_research_journal_closeout_phase1.py",
)


def _find_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parent]
    home = Path.home()
    candidates.extend((home / "Desktop" / "jarvis", home / "Desktop" / "artmach_assistant"))
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve(strict=False)
        for current in (candidate, *candidate.parents):
            if current in seen:
                continue
            seen.add(current)
            if (current / "core").is_dir() and (current / "tests").is_dir():
                return current
    raise SystemExit("PROJE KOKU BULUNAMADI. Scripti jarvis/artmach_assistant klasorunde calistirin.")


def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        args,
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )


def _pytest_counts(output: str) -> dict[str, int]:
    result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0}
    aliases = {"errors": ("error", "errors")}
    for key in result:
        labels = aliases.get(key, (key.rstrip("s"), key))
        values = [int(value) for label in labels for value in re.findall(rf"(?<!\d)(\d+)\s+{label}\b", output)]
        if values:
            result[key] = max(values)
    return result


def _failed_nodeids(output: str) -> tuple[str, ...]:
    items: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        match = re.match(r"^(?:FAILED|ERROR)\s+([^\s]+)", stripped)
        if match:
            items.append(match.group(1))
    return tuple(dict.fromkeys(items))


def _write_report(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    root = _find_root()
    report_path = root.parent / REPORT_NAME
    log_path = root.parent / LOG_NAME
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    required = [root / "core" / "self_improvement_research.py", root / "core" / "notification_store.py"]
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        payload = {"status": "BLOCKED", "reason": "required_files_missing", "missing": missing, "project_root": str(root)}
        _write_report(report_path, payload)
        print("GEREKLI DOSYALAR EKSIK: " + ", ".join(missing))
        print(f"RAPOR: {report_path}")
        return 2

    compile_targets = [str(path) for path in required]
    compile_targets.extend(str(root / item) for item in FOCUSED_TESTS if (root / item).is_file())
    compile_result = _run(root, [sys.executable, "-m", "py_compile", *compile_targets])
    if compile_result.returncode:
        payload = {
            "status": "FAILED",
            "phase": "compile",
            "project_root": str(root),
            "output": compile_result.stdout,
            "started_at": started_at,
        }
        _write_report(report_path, payload)
        print("DERLEME BASARISIZ")
        print(compile_result.stdout)
        print(f"RAPOR: {report_path}")
        return 1

    available_focused = [item for item in FOCUSED_TESTS if (root / item).is_file()]
    focused_result = _run(root, [sys.executable, "-m", "pytest", "-q", *available_focused])
    focused_counts = _pytest_counts(focused_result.stdout)
    if focused_result.returncode:
        payload = {
            "status": "FAILED",
            "phase": "focused_tests",
            "project_root": str(root),
            "tests": available_focused,
            "counts": focused_counts,
            "failed_nodeids": _failed_nodeids(focused_result.stdout),
            "output": focused_result.stdout,
            "started_at": started_at,
        }
        _write_report(report_path, payload)
        log_path.write_text(focused_result.stdout, encoding="utf-8")
        print("ODAK TEST BASARISIZ")
        print(focused_result.stdout)
        print(f"RAPOR: {report_path}")
        print(f"LOG: {log_path}")
        return 1

    full_result = _run(root, [sys.executable, "-m", "pytest", "-q"])
    log_path.write_text(full_result.stdout, encoding="utf-8")
    full_counts = _pytest_counts(full_result.stdout)
    failed_nodeids = _failed_nodeids(full_result.stdout)
    status = "COMPLETE" if full_result.returncode == 0 and not failed_nodeids else "FAILED"
    payload = {
        "phase": "Research Engine Final Regression Closeout",
        "status": status,
        "project_root": str(root),
        "python": sys.executable,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_files_modified": False,
        "compile": {"ok": True},
        "focused_tests": {
            "ok": True,
            "tests": available_focused,
            "counts": focused_counts,
            "output": focused_result.stdout,
        },
        "full_tests": {
            "ok": status == "COMPLETE",
            "returncode": full_result.returncode,
            "counts": full_counts,
            "failed_nodeids": failed_nodeids,
            "log_path": str(log_path),
        },
        "research_engine_complete": status == "COMPLETE",
    }
    _write_report(report_path, payload)

    if status != "COMPLETE":
        print("TAM TEST BASARISIZ")
        print(full_result.stdout)
        print("KAYNAK DOSYALAR DEGISTIRILMEDI; GERI ALMA GEREKMIYOR")
        print(f"RAPOR: {report_path}")
        print(f"LOG: {log_path}")
        return 1

    summary = full_result.stdout.strip().splitlines()[-1] if full_result.stdout.strip() else "OK"
    print("RESEARCH ENGINE FINAL REGRESYON KAPANISI TAMAMLANDI")
    print(f"ODAK TEST: {focused_result.stdout.strip().splitlines()[-1] if focused_result.stdout.strip() else 'OK'}")
    print(f"TAM TEST: {summary}")
    print(f"RAPOR: {report_path}")
    print(f"LOG: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
