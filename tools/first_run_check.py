from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _bootstrap() -> Path:
    package = Path(__file__).resolve().parents[1]
    parent = package.parent
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))
    return package


def _data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ArtmachAssistant"
    return Path.home() / ".local" / "share" / "ArtmachAssistant"


def main() -> int:
    project_default = _bootstrap()
    from artmach_assistant.core.final_release import run_first_run_checks, save_first_run_report

    parser = argparse.ArgumentParser(description="Jarvis ilk acilis kontrolleri.")
    parser.add_argument("--project-root", default=str(project_default))
    parser.add_argument("--acceptance-report", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_first_run_checks(
        args.project_root,
        acceptance_report=args.acceptance_report or None,
    )
    target = _data_root() / "first_run.json"
    save_first_run_report(report, target)
    if args.json:
        import json
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("JARVIS ILK ACILIS KONTROLU")
        for item in report.checks:
            marker = "OK" if item.state == "passed" else ("UYARI" if item.state == "warning" else "HATA")
            print(f"[{marker}] {item.name}: {item.detail}")
        print(f"Hazir: {'evet' if report.ready else 'hayir'}")
        print(f"Kayit: {target}")
    return 0 if report.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
