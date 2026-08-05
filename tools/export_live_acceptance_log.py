from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _desktop() -> Path:
    candidate = Path.home() / "Desktop"
    return candidate if candidate.exists() else Path.cwd()


def _read_events(path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"event": "INVALID_JSON", "detail": line}
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _run(text: str, *, bold: bool = False, size_half_points: int | None = None, mono: bool = False) -> str:
    props: list[str] = []
    if bold:
        props.append("<w:b/>")
    if size_half_points is not None:
        props.append(f'<w:sz w:val="{size_half_points}"/><w:szCs w:val="{size_half_points}"/>')
    if mono:
        props.append('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>')
    prop_xml = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    safe = escape(str(text))
    return f'<w:r>{prop_xml}<w:t xml:space="preserve">{safe}</w:t></w:r>'


def _paragraph(text: str = "", *, bold: bool = False, center: bool = False, size_half_points: int | None = None, mono: bool = False, keep_next: bool = False) -> str:
    p_props: list[str] = []
    if center:
        p_props.append('<w:jc w:val="center"/>')
    if keep_next:
        p_props.append("<w:keepNext/>")
    ppr = f"<w:pPr>{''.join(p_props)}</w:pPr>" if p_props else ""
    return f"<w:p>{ppr}{_run(text, bold=bold, size_half_points=size_half_points, mono=mono)}</w:p>"


def _heading(text: str, level: int = 1) -> str:
    size = 30 if level == 1 else 26
    return _paragraph(text, bold=True, size_half_points=size, keep_next=True)


def _cell(text: str, *, bold: bool = False) -> str:
    return (
        '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
        + _paragraph(text, bold=bold, size_half_points=18)
        + "</w:tc>"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    borders = (
        '<w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:color="B7C3D0"/>'
        '<w:left w:val="single" w:sz="4" w:color="B7C3D0"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="B7C3D0"/>'
        '<w:right w:val="single" w:sz="4" w:color="B7C3D0"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="D7DEE7"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="D7DEE7"/>'
        '</w:tblBorders>'
    )
    parts = [f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>{borders}</w:tblPr>']
    parts.append("<w:tr>" + "".join(_cell(value, bold=True) for value in headers) + "</w:tr>")
    for row in rows:
        parts.append("<w:tr>" + "".join(_cell(value) for value in row) + "</w:tr>")
    parts.append("</w:tbl>")
    return "".join(parts)


def _document_xml(events: list[dict[str, object]], source: Path) -> str:
    counts = Counter(str(row.get("event", "UNKNOWN")) for row in events)
    frequent = ", ".join(f"{name} ({count})" for name, count in counts.most_common(10)) or "Kayit yok"
    important_names = {
        "TEXT_SUBMITTED", "GUI_BUSY", "LIVE_QUERY_CLASSIFIED", "STATUS_READ",
        "RESPONSE_RENDERED", "SUBMIT_HANDLER_ENTERED", "INPUT_RETURN_PRESSED",
        "SEND_BUTTON_CLICKED", "OPERATION_STARTED", "OPERATION_UPDATED",
        "OPERATION_FINISHED", "SHUTDOWN_WORKER_WAIT", "SHUTDOWN_WORKER_FORCE_STOP",
        "SHUTDOWN_WORKER_ERROR", "SHUTDOWN_FINISHED", "GUI_EVENT_LOOP_HEARTBEAT",
        "INPUT_TEXT_CHANGED",
    }
    important = [row for row in events if str(row.get("event")) in important_names]
    body: list[str] = []
    body.append(_paragraph("Jarvis Canli Kabul Testi Logu", bold=True, center=True, size_half_points=36))
    body.append(_paragraph(f"Kaynak: {source}", bold=True, center=True, size_half_points=20))
    body.append(_paragraph(f"Olusturma: {datetime.now().astimezone().isoformat(timespec='seconds')}", center=True, size_half_points=18))
    body.append(_heading("Ozet"))
    body.append(_paragraph(f"Toplam olay: {len(events)}", size_half_points=22))
    body.append(_paragraph(f"En sik olaylar: {frequent}", size_half_points=20))
    body.append(_heading("Onemli Olay Akisi"))
    rows: list[list[str]] = []
    for row in important[-250:]:
        detail = {
            key: value
            for key, value in row.items()
            if key not in {"timestamp", "event", "thread", "monotonic"}
        }
        rows.append([
            str(row.get("timestamp", "")),
            str(row.get("event", "")),
            str(row.get("thread", "")),
            json.dumps(detail, ensure_ascii=False, sort_keys=True),
        ])
    body.append(_table(["Zaman", "Olay", "Thread", "Ayrinti"], rows))
    body.append(_heading("Ham Log"))
    for row in events:
        body.append(_paragraph(json.dumps(row, ensure_ascii=False, sort_keys=True), mono=True, size_half_points=16))
    body.append(
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1008" w:right="1080" w:bottom="1008" w:left="1080" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>{"".join(body)}</w:body></w:document>'
    )


def _write_docx(output: Path, document_xml: str) -> None:
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>Jarvis Canli Kabul Testi Logu</dc:title><dc:creator>Jarvis</dc:creator>
<dcterms:created xsi:type="dcterms:W3CDTF">{datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")}</dcterms:created>
</cp:coreProperties>'''
    app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Jarvis</Application></Properties>'''
    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'''
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)


def export_log(source: Path, output: Path) -> Path:
    events = _read_events(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_docx(output, _document_xml(events, source))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="logs/live_acceptance_trace.jsonl")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_file():
        print(f"ERROR: log file not found: {source}")
        return 1

    if args.output:
        output = Path(args.output).resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = _desktop() / f"Jarvis_Canli_Kabul_Logu_{stamp}.docx"

    created = export_log(source, output)
    print(f"OK: {created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
