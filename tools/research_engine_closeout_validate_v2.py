from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


BASELINE_PASSED = 1373
BASELINE_SKIPPED = 7

FOCUSED_TEST_PATTERNS = (
    "tests/test_self_improvement_research.py",
    "tests/test_self_improvement_research_*.py",
    "tests/test_research_engine*.py",
)


def _project_root() -> Path:
    # tools/research_engine_closeout_validate.py -> project root
    return Path(__file__).resolve().parents[1]


def _package_context(project_root: Path) -> tuple[Path, str]:
    """
    Return (working_directory, package_name).

    The repository is itself the Python package directory:
      Desktop/
        artmach_assistant/
          __init__.py
          core/
          tests/

    Therefore imports such as artmach_assistant.core.* require Desktop on
    sys.path and pytest must run from Desktop, not from artmach_assistant.
    """
    if (project_root / "__init__.py").is_file():
        return project_root.parent, project_root.name

    # Fallback for a repository that contains a nested package directory.
    candidates = [
        child for child in project_root.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    ]
    for candidate in candidates:
        if (candidate / "core").is_dir():
            return project_root, candidate.name

    raise RuntimeError(
        "Python paket kökü bulunamadı. Proje kökünde __init__.py veya "
        "core içeren bir paket klasörü bekleniyordu."
    )


def _environment(workdir: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    entries = [str(workdir)]
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    env["PYTHONUTF8"] = "1"
    return env


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _parse_pytest_summary(output: str) -> dict[str, int | None]:
    counts: dict[str, int | None] = {
        "passed": None,
        "failed": None,
        "errors": None,
        "skipped": None,
    }

    patterns = {
        "passed": r"(\d+)\s+passed",
        "failed": r"(\d+)\s+failed",
        "errors": r"(\d+)\s+errors?",
        "skipped": r"(\d+)\s+skipped",
    }
    for key, pattern in patterns.items():
        matches = re.findall(pattern, output, flags=re.IGNORECASE)
        if matches:
            counts[key] = int(matches[-1])

    # Pytest can stop during collection and only print "40 errors".
    if counts["errors"] is None:
        collection_match = re.search(
            r"Interrupted:\s*(\d+)\s+errors?\s+during\s+collection",
            output,
            flags=re.IGNORECASE,
        )
        if collection_match:
            counts["errors"] = int(collection_match.group(1))

    return counts


def _focused_tests(project_root: Path, package_name: str) -> list[str]:
    selected: list[Path] = []
    for pattern in FOCUSED_TEST_PATTERNS:
        selected.extend(project_root.glob(pattern))

    unique = sorted({path.resolve() for path in selected if path.is_file()})
    if not unique:
        raise RuntimeError(
            "Research Engine odaklı test dosyaları bulunamadı. "
            "Beklenen örnek: tests/test_self_improvement_research.py"
        )

    # Paths are passed relative to the package parent working directory.
    return [
        str(Path(package_name) / path.relative_to(project_root))
        for path in unique
    ]


def _compile_sources(
    project_root: Path,
    workdir: Path,
    package_name: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    sources = [
        project_root / "core" / "assistant.py",
        project_root / "core" / "self_improvement_research.py",
    ]
    optional = [
        project_root / "core" / "self_improvement_experiment_contract.py",
        project_root / "core" / "self_improvement_evidence.py",
    ]
    sources.extend(path for path in optional if path.is_file())

    missing = [str(path) for path in sources[:2] if not path.is_file()]
    if missing:
        raise RuntimeError("Zorunlu kaynak dosyaları eksik: " + ", ".join(missing))

    relative_sources = [
        str(Path(package_name) / path.relative_to(project_root))
        for path in sources
    ]
    return _run(
        [sys.executable, "-m", "py_compile", *relative_sources],
        cwd=workdir,
        env=env,
    )


def main() -> int:
    project_root = _project_root()
    workdir, package_name = _package_context(project_root)
    env = _environment(workdir)
    report_path = project_root / "research_engine_closeout_report.json"

    print("Research Engine kapanış doğrulaması")
    print(f"Proje paketi: {package_name}")
    print(f"Test çalışma dizini: {workdir}")

    report: dict[str, Any] = {
        "project_root": str(project_root),
        "working_directory": str(workdir),
        "package_name": package_name,
        "baseline": {
            "passed": BASELINE_PASSED,
            "skipped": BASELINE_SKIPPED,
        },
    }

    try:
        compile_result = _compile_sources(
            project_root, workdir, package_name, env
        )
    except Exception as exc:
        report["compile"] = {"ok": False, "error": str(exc)}
        report["status"] = "NOT_COMPLETE"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Derleme: BAŞARISIZ ({exc})")
        print("Research Engine durumu: NOT_COMPLETE")
        return 1

    compile_ok = compile_result.returncode == 0
    report["compile"] = {
        "ok": compile_ok,
        "returncode": compile_result.returncode,
        "output": compile_result.stdout,
    }
    print("Derleme:", "GEÇTİ" if compile_ok else "BAŞARISIZ")
    if not compile_ok:
        report["status"] = "NOT_COMPLETE"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(compile_result.stdout)
        print("Research Engine durumu: NOT_COMPLETE")
        return 1

    try:
        focused_paths = _focused_tests(project_root, package_name)
    except Exception as exc:
        report["focused_tests"] = {"ok": False, "error": str(exc)}
        report["status"] = "NOT_COMPLETE"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Odaklı testler: BAŞARISIZ ({exc})")
        print("Research Engine durumu: NOT_COMPLETE")
        return 1

    focused_result = _run(
        [sys.executable, "-m", "pytest", "-q", *focused_paths],
        cwd=workdir,
        env=env,
    )
    focused_counts = _parse_pytest_summary(focused_result.stdout)
    focused_ok = focused_result.returncode == 0
    report["focused_tests"] = {
        "ok": focused_ok,
        "returncode": focused_result.returncode,
        "counts": focused_counts,
        "paths": focused_paths,
        "output": focused_result.stdout,
    }
    print("Odaklı testler:", "GEÇTİ" if focused_ok else "BAŞARISIZ")
    if not focused_ok:
        report["status"] = "NOT_COMPLETE"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(focused_result.stdout[-12000:])
        print("Research Engine durumu: NOT_COMPLETE")
        return 1

    full_test_path = str(Path(package_name) / "tests")
    full_result = _run(
        [sys.executable, "-m", "pytest", "-q", full_test_path],
        cwd=workdir,
        env=env,
    )
    full_counts = _parse_pytest_summary(full_result.stdout)
    full_ok = full_result.returncode == 0

    passed = full_counts["passed"]
    skipped = full_counts["skipped"] or 0
    failed = full_counts["failed"] or 0
    errors = full_counts["errors"] or 0

    baseline_ok = bool(
        full_ok
        and passed is not None
        and passed >= BASELINE_PASSED
        and skipped >= 0
        and failed == 0
        and errors == 0
    )

    report["full_tests"] = {
        "ok": full_ok,
        "returncode": full_result.returncode,
        "counts": full_counts,
        "output": full_result.stdout,
    }
    report["baseline_comparison"] = {
        "ok": baseline_ok,
        "minimum_passed": BASELINE_PASSED,
        "reference_skipped": BASELINE_SKIPPED,
        "actual_passed": passed,
        "actual_skipped": skipped,
        "actual_failed": failed,
        "actual_errors": errors,
        "note": (
            "Yeni testler eklendiği için passed sayısı baseline'dan yüksek olabilir. "
            "Skipped sayısının birebir eşit olması zorunlu değildir; başarısızlık "
            "ve collection error olmaması zorunludur."
        ),
    }

    print("Tam test paketi:", "GEÇTİ" if full_ok else "BAŞARISIZ")
    print(
        f"Sonuç: passed={passed}, skipped={skipped}, "
        f"failed={failed}, errors={errors}"
    )
    print(
        "Baseline karşılaştırması:",
        "GEÇTİ" if baseline_ok else "REGRESSION_OR_TEST_FAILURE",
    )

    complete = compile_ok and focused_ok and full_ok and baseline_ok
    report["status"] = "COMPLETE" if complete else "NOT_COMPLETE"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        "Research Engine durumu:",
        "COMPLETE" if complete else "NOT_COMPLETE",
    )
    print(f"Ayrıntılı rapor: {report_path}")

    if not full_ok:
        print("\nSon tam test çıktısı:\n")
        print(full_result.stdout[-16000:])

    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
