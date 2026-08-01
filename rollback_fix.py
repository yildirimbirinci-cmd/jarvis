from __future__ import annotations
import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    backup_root = root / ".jarvis_backups"
    candidates = sorted(backup_root.glob("self_development_runtime_fix_*"), reverse=True)
    if not candidates:
        raise FileNotFoundError("Geri alınacak öz-geliştirme düzeltmesi yedeği bulunamadı.")
    backup = candidates[0]
    shutil.copy2(backup / "own_code_intent.py", root / "core" / "own_code_intent.py")
    shutil.copy2(backup / "assistant.py", root / "core" / "assistant.py")
    test_path = root / "tests" / "test_self_development_preview_routing.py"
    test_path.unlink(missing_ok=True)
    print(f"Geri alındı: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
