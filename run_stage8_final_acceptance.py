from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

COMPILE_TARGETS = [
    ROOT / "app.py",
    ROOT / "core" / "assistant.py",
    ROOT / "core" / "task_orchestrator.py",
    ROOT / "core" / "live_operation_dialogue.py",
]

TARGET_TESTS = [
    "tests/test_stage8_final_acceptance_contract.py",
    "tests/test_stage8_final_fifo_runtime_handoff_v12.py",
    "tests/test_stage8_gui_queue_contract.py",
    "tests/test_stage8_task_orchestrator_queue.py",
    "tests/test_stage8_task_isolation_state_integrity_v10.py",
    "tests/test_stage8_task_isolation_gui_contract_v10.py",
    "tests/test_stage8_cancel_fast_path_compat.py",
    "tests/test_stage8_cancel_fast_path_race.py",
    "tests/test_stage8_cancel_pending_fast_path.py",
    "tests/test_stage8_live_cancel_phrase.py",
    "tests/test_stage8_live_fast_path_compat_v7.py",
    "tests/test_stage8_live_fast_path_regression_v6.py",
    "tests/test_stage8_collaborative_option_routing.py",
]


def run(args: list[str]) -> None:
    print("RUN:", " ".join(args), flush=True)
    completed = subprocess.run(args, cwd=ROOT)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    print("STAGE 8 FINAL ACCEPTANCE", flush=True)
    print("1/3 compile", flush=True)
    for path in COMPILE_TARGETS:
        if not path.exists():
            raise SystemExit(f"ERROR: missing required source: {path.relative_to(ROOT)}")
        py_compile.compile(str(path), doraise=True)
    print("OK: compile", flush=True)

    missing = [name for name in TARGET_TESTS if not (ROOT / name).exists()]
    if missing:
        raise SystemExit("ERROR: missing target tests:\n" + "\n".join(missing))

    print("2/3 targeted Stage 8 regression", flush=True)
    run([PY, "-m", "pytest", *TARGET_TESTS, "-q"])
    print("OK: targeted Stage 8 regression", flush=True)

    print("3/3 full regression", flush=True)
    run([PY, "-m", "pytest", "-q"])
    print("OK: full regression", flush=True)

    print("STAGE 8 ACCEPTANCE: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
