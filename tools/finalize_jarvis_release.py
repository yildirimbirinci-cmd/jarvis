from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _bootstrap() -> Path:
    project = Path(__file__).resolve().parents[1]
    parent = project.parent
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))
    return project


def main() -> int:
    project_default = _bootstrap()
    from artmach_assistant.core.final_release import (
        FinalReleaseError,
        build_final_release,
        default_acceptance_path,
    )

    parser = argparse.ArgumentParser(description="Jarvis nihai kaynak teslimini üretir.")
    parser.add_argument("--project-root", default=str(project_default))
    parser.add_argument("--acceptance-report", default=str(default_acceptance_path()))
    parser.add_argument("--output-dir", default=str(project_default.parent / "final_release"))
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    try:
        result = build_final_release(
            args.project_root,
            args.acceptance_report,
            args.output_dir,
            version=args.version,
        )
    except FinalReleaseError as exc:
        print(f"FINAL TESLIM OLUSTURULAMADI: {exc}", file=sys.stderr)
        return 2
    print("JARVIS FINAL TESLIM HAZIR")
    print(f"Release: {result['release_id']}")
    print(f"Kaynak ZIP: {result['source_zip']}")
    print(f"Geri donus ZIP: {result['rollback_zip']}")
    print(f"Manifest: {result['manifest']}")
    print(f"Dosya sayisi: {result['file_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
