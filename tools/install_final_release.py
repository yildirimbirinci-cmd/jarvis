from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _bootstrap() -> None:
    package = Path(__file__).resolve().parents[1]
    parent = package.parent
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))


def main() -> int:
    _bootstrap()
    from artmach_assistant.core.final_release import FinalReleaseError, install_release

    parser = argparse.ArgumentParser(description="Jarvis final release kurulum/guncelleme araci.")
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--python", default="")
    args = parser.parse_args()
    try:
        result = install_release(
            args.release_root,
            args.destination,
            backup_dir=args.backup_dir or None,
            compile_python=args.python or None,
        )
    except (FinalReleaseError, OSError) as exc:
        print(f"KURULUM BASARISIZ: {exc}", file=sys.stderr)
        return 2
    print("KURULUM BASARILI")
    print(f"Hedef: {result['destination']}")
    if result["backup"]:
        print(f"Geri donus: {result['backup']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
