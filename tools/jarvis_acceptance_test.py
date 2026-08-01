from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

DESKTOP = Path.home() / "Desktop"
OUTPUT_ROOT = DESKTOP / "test_jarvis"


def now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + f"_{int((time.time() % 1) * 1_000_000):06d}"


def find_package_root() -> Path:
    start = Path(__file__).resolve()
    candidates = [start.parent, *start.parents]
    for base in candidates:
        if (base / "app.py").is_file() and (base / "core").is_dir() and (base / "tests").is_dir():
            return base
        nested = base / "artmach_assistant"
        if (nested / "app.py").is_file() and (nested / "core").is_dir() and (nested / "tests").is_dir():
            return nested
    raise RuntimeError("artmach_assistant proje kok dizini bulunamadi.")


def candidate_pythons(package_root: Path) -> list[Path]:
    result: list[Path] = []
    roots = [package_root, package_root.parent, package_root.parent.parent]
    for root in roots:
        for name in ("venv", ".venv", "env", ".env"):
            result.append(root / name / "Scripts" / "python.exe")
    result.append(Path(sys.executable))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in result:
        key = str(path).lower()
        if key not in seen and path.is_file():
            seen.add(key)
            unique.append(path)
    return unique


def probe_python(executable: Path, package_parent: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(package_parent), env.get("PYTHONPATH", "")]))
    code = (
        "import importlib.util,json,sys;"
        "mods=['pytest','PySide6','artmach_assistant'];"
        "print(json.dumps({'exe':sys.executable,'mods':{m:(importlib.util.find_spec(m) is not None) for m in mods}}))"
    )
    completed = subprocess.run(
        [str(executable), "-c", code], cwd=package_parent, env=env,
        text=True, capture_output=True, timeout=20, check=False,
    )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else {}
    except Exception:
        payload = {}
    payload.update({"returncode": completed.returncode, "stderr": completed.stderr[-2000:]})
    return payload


def select_python(package_root: Path, run_dir: Path) -> Path:
    package_parent = package_root.parent
    probes = []
    selected: Path | None = None
    fallback: Path | None = None
    for executable in candidate_pythons(package_root):
        probe = probe_python(executable, package_parent)
        probe["candidate"] = str(executable)
        probes.append(probe)
        mods = probe.get("mods") or {}
        if probe.get("returncode") == 0 and mods.get("artmach_assistant"):
            fallback = fallback or executable
            if mods.get("PySide6") and mods.get("pytest"):
                selected = executable
                break
    (run_dir / "python_detection.json").write_text(json.dumps(probes, indent=2, ensure_ascii=False), encoding="utf-8")
    selected = selected or fallback
    if selected is None:
        raise RuntimeError("Projeyi import edebilen bir Python ortami bulunamadi.")
    return selected


def ensure_pytest(python: Path, package_parent: Path, log_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(package_parent), env.get("PYTHONPATH", "")]))
    check = subprocess.run([str(python), "-c", "import pytest"], cwd=package_parent, env=env, check=False)
    if check.returncode == 0:
        log_path.write_text("pytest zaten kurulu.\n", encoding="utf-8")
        return
    completed = subprocess.run(
        [str(python), "-m", "pip", "install", "pytest"], cwd=package_parent, env=env,
        text=True, capture_output=True, check=False,
    )
    log_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError("pytest kurulumu basarisiz. dependency_setup.log dosyasina bak.")


def run_compile(python: Path, package_root: Path, run_dir: Path, env: dict[str, str]) -> int:
    completed = subprocess.run(
        [str(python), "-m", "compileall", "-q", str(package_root)],
        cwd=package_root.parent, env=env, text=True, capture_output=True, check=False,
    )
    (run_dir / "compile.log").write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    return completed.returncode


def run_tests(python: Path, package_root: Path, run_dir: Path, env: dict[str, str]) -> int:
    completed = subprocess.run(
        [
            str(python), "-m", "artmach_assistant",
            "--self-test", "--quiet-tests",
        ],
        cwd=package_root.parent, env=env,
        text=True, capture_output=True, check=False,
    )
    (run_dir / "project_tests.log").write_text(
        completed.stdout + "\n" + completed.stderr,
        encoding="utf-8",
    )
    return completed.returncode


def launch_jarvis(python: Path, package_root: Path, run_dir: Path, env: dict[str, str]) -> tuple[bool, int | None]:
    log_file = run_dir / "jarvis_launch.log"
    command = [str(python), "-m", "artmach_assistant", "--gui-smoke-test"]
    smoke_env = dict(env)
    smoke_env.setdefault("QT_QPA_PLATFORM", "offscreen")
    with log_file.open("w", encoding="utf-8", errors="replace") as handle:
        completed = subprocess.run(
            command,
            cwd=package_root.parent,
            env=smoke_env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
    alive = completed.returncode == 0
    code = completed.returncode
    (run_dir / "jarvis_process.json").write_text(
        json.dumps(
            {
                "command": command,
                "gui_smoke_passed": alive,
                "returncode": code,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return alive, code


def create_workspace(run_dir: Path) -> None:
    workspace = OUTPUT_ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    sample = workspace / "sample_project"
    if sample.exists():
        shutil.rmtree(sample)
    sample.mkdir(parents=True)
    (sample / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (sample / "config.json").write_text(json.dumps({"name": "jarvis_acceptance"}, indent=2), encoding="utf-8")
    shutil.copy2(sample / "main.py", sample / "main_copy.py")
    (sample / "main_copy.py").rename(sample / "renamed.py")
    (sample / "renamed.py").write_text("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n", encoding="utf-8")
    (run_dir / "workspace_path.txt").write_text(str(sample), encoding="utf-8")


def make_zip(run_dir: Path) -> Path:
    archive = OUTPUT_ROOT / f"{run_dir.name}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in run_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(run_dir.parent))
    return archive


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = OUTPUT_ROOT / f"run_{now_stamp()}"
    run_dir.mkdir(parents=True)
    summary: dict[str, object] = {}
    exit_code = 1
    try:
        package_root = find_package_root()
        package_parent = package_root.parent
        summary["package_root"] = str(package_root)
        python = select_python(package_root, run_dir)
        summary["python"] = str(python)
        ensure_pytest(python, package_parent, run_dir / "dependency_setup.log")
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(package_parent), env.get("PYTHONPATH", "")]))
        env["PYTHONUNBUFFERED"] = "1"
        create_workspace(run_dir)
        compile_code = run_compile(python, package_root, run_dir, env)
        test_code = run_tests(python, package_root, run_dir, env)
        gui_smoke_passed, gui_smoke_code = launch_jarvis(
            python,
            package_root,
            run_dir,
            env,
        )
        summary.update({
            "compile_returncode": compile_code,
            "tests_returncode": test_code,
            "gui_smoke_passed": gui_smoke_passed,
            "gui_smoke_returncode": gui_smoke_code,
        })
        # Test failures are real project failures; runner success means setup and launch worked.
        summary["runner_result"] = (
            "OK" if compile_code == 0 and gui_smoke_passed else "FAILED"
        )
        summary["project_tests_result"] = "PASSED" if test_code == 0 else "FAILED"
        exit_code = 0 if compile_code == 0 and gui_smoke_passed else 1
    except Exception as exc:
        summary["runner_result"] = "FAILED"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        (run_dir / "fatal_error.txt").write_text(summary["error"], encoding="utf-8")
    finally:
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        archive = make_zip(run_dir)
        print("\nGONDERILECEK ZIP:")
        print(archive)
        print("\nOZET:")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
