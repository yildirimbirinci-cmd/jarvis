from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OLD = '''        collaborative_problem = self._collaborative_problem_request(text)
        if collaborative_problem is not None:
            return collaborative_problem
        plan_follow_up = self._handle_own_code_plan_follow_up(text)
        if plan_follow_up is not None:
            return plan_follow_up
'''

NEW = '''        # Kayıtlı kendi-kod planı, açık kalmış genel problem çözme
        # oturumundan önce işlenmelidir. Aksi halde kısa ve deterministik
        # 'planı onayla' komutu sohbet/problem çözme katmanı tarafından
        # yakalanır ve gerçek patch üretimi başlamaz.
        plan_follow_up = self._handle_own_code_plan_follow_up(text)
        if plan_follow_up is not None:
            return plan_follow_up
        collaborative_problem = self._collaborative_problem_request(text)
        if collaborative_problem is not None:
            return collaborative_problem
'''

def fail(message: str) -> None:
    print(f"\nHATA: {message}")
    raise SystemExit(1)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    project = args.root.expanduser().resolve()
    assistant = project / "core" / "assistant.py"
    tests_dir = project / "tests"

    if not assistant.is_file() or not tests_dir.is_dir():
        fail(f"Jarvis proje yapısı bulunamadı: {project}")

    source = assistant.read_text(encoding="utf-8")
    if NEW not in source:
        count = source.count(OLD)
        if count != 1:
            fail(f"Güvenli patch hedefi 1 kez bulunmalıydı; bulunan: {count}")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = project / ".jarvis_fix_backup" / f"plan_approval_priority_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assistant, backup_dir / "assistant.py")

        source = source.replace(OLD, NEW, 1)
        assistant.write_text(source, encoding="utf-8", newline="\n")
    else:
        print("Düzeltme zaten kurulu.")

    test_file = tests_dir / "test_own_code_plan_approval_priority.py"
    test_source = '''from pathlib import Path

def test_own_code_plan_follow_up_precedes_collaborative_problem() -> None:
    package_root = Path(__file__).resolve().parents[1]
    source = (package_root / "core" / "assistant.py").read_text(encoding="utf-8")
    plan_position = source.index(
        "plan_follow_up = self._handle_own_code_plan_follow_up(text)"
    )
    collaborative_position = source.index(
        "collaborative_problem = self._collaborative_problem_request(text)"
    )
    assert plan_position < collaborative_position

def test_short_plan_approval_has_deterministic_fallback() -> None:
    package_root = Path(__file__).resolve().parents[1]
    source = (package_root / "core" / "assistant.py").read_text(encoding="utf-8")
    assert '"planı onayla"' in source
    assert '"plani onayla"' in source
'''
    test_file.write_text(test_source, encoding="utf-8", newline="\n")

    commands = [
        [sys.executable, "-m", "py_compile", str(assistant)],
        [sys.executable, "-m", "pytest", "-q", str(test_file)],
    ]
    for command in commands:
        print("\n>", " ".join(command))
        result = subprocess.run(command, cwd=project.parent)
        if result.returncode != 0:
            fail("Kontrol başarısız. assistant.py yedeği .jarvis_fix_backup içindedir.")

    print("\nJARVIS PLAN ONAY ONCELIK DUZELTMESI BASARILI.")
    print("Degisen dosyalar:")
    print("- core/assistant.py")
    print("- tests/test_own_code_plan_approval_priority.py")

if __name__ == "__main__":
    main()
