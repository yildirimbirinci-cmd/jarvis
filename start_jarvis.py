from __future__ import annotations

"""Artmach Assistant'i paket klasorunun icinden guvenli bicimde baslatir.

Bu dosya su konumda bulunmalidir:
    Artmach_Asistant_Program/artmach_assistant/start_jarvis.py

Calistirma:
    python start_jarvis.py
"""

import sys
from pathlib import Path


def _prepare_package_path() -> Path:
    package_dir = Path(__file__).resolve().parent
    project_root = package_dir.parent

    root_text = str(project_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    return project_root


def main() -> None:
    _prepare_package_path()

    from artmach_assistant.app import main as application_main

    raise SystemExit(
        application_main(background="--background" in sys.argv[1:])
    )


if __name__ == "__main__":
    main()
