from __future__ import annotations

import json
import zipfile
from pathlib import Path

from artmach_assistant.core.support_bundle import create_support_bundle


def test_support_bundle_includes_latest_end_to_end_report(tmp_path: Path) -> None:
    data = tmp_path / "data"
    report = data / "logs" / "acceptance" / "e2e_latest.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps({"run_id": "E2E-TEST", "ready": False}),
        encoding="utf-8",
    )

    bundle = create_support_bundle(data, tmp_path / "support.zip")

    with zipfile.ZipFile(bundle) as archive:
        assert "logs/acceptance/e2e_latest.json" in archive.namelist()
        payload = json.loads(archive.read("logs/acceptance/e2e_latest.json"))
    assert payload["run_id"] == "E2E-TEST"
