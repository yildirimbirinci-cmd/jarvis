from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools.export_live_acceptance_log import export_log


def test_export_log_creates_readable_docx_without_external_dependency(tmp_path: Path) -> None:
    source = tmp_path / "trace.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"timestamp": "now", "event": "TEXT_SUBMITTED", "thread": "MainThread"},
                {"timestamp": "later", "event": "RESPONSE_RENDERED", "thread": "MainThread"},
            )
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report.docx"
    export_log(source, output)

    assert output.is_file()
    assert zipfile.is_zipfile(output)
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "[Content_Types].xml" in names
        assert "word/document.xml" in names
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Jarvis Canli Kabul Testi Logu" in document_xml
    assert "Toplam olay: 2" in document_xml
    assert "TEXT_SUBMITTED" in document_xml
    assert "RESPONSE_RENDERED" in document_xml
