from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedFileCommand:
    action: str
    source: str = ""
    destination: str = ""
    new_name: str = ""


_QUOTED = r'["\']([^"\']+)["\']'
_WINDOWS_PATH = r'([A-Za-z]:\\[^\n\r]+?)'


def _clean(value: str) -> str:
    return str(value or "").strip().strip('"').strip("'").rstrip(".,;:")


def parse_file_command(text: str) -> ParsedFileCommand | None:
    """Parse explicit copy, move and rename requests without guessing paths.

    The parser intentionally accepts only commands that expose both required
    operands. Missing information is handled by Assistant's dialogue state.
    """
    raw = str(text or "").strip()
    lowered = raw.casefold()
    action = ""
    if any(word in lowered for word in ("kopyala", "copy")):
        action = "copy"
    elif any(word in lowered for word in ("taşı", "tasi", "move")):
        action = "move"
    elif any(word in lowered for word in ("yeniden adlandır", "yeniden adlandir", "adını değiştir", "adini degistir", "rename")):
        action = "rename"
    if not action:
        return None

    quoted = [_clean(value) for value in re.findall(_QUOTED, raw)]
    if action in {"copy", "move"} and len(quoted) >= 2:
        return ParsedFileCommand(action=action, source=quoted[0], destination=quoted[1])
    if action == "rename" and len(quoted) >= 2:
        return ParsedFileCommand(action=action, source=quoted[0], new_name=quoted[1])

    # Windows-friendly unquoted form:
    # C:\\path\\file.txt dosyasını C:\\target klasörüne kopyala
    if action in {"copy", "move"}:
        match = re.search(
            r'(?P<src>[A-Za-z]:\\.+?)\s+(?:dosyasını|dosyasini|klasörünü|klasorunu|öğesini|ogesini)?\s*'
            r'(?P<dst>[A-Za-z]:\\.+?)\s+(?:klasörüne|klasorune|içine|icine)?\s*'
            r'(?:kopyala|taşı|tasi|copy|move)\s*$',
            raw,
            flags=re.IGNORECASE,
        )
        if match:
            return ParsedFileCommand(action=action, source=_clean(match.group("src")), destination=_clean(match.group("dst")))
    else:
        match = re.search(
            r'(?P<src>[A-Za-z]:\\.+?)\s+(?:dosyasının|dosyasinin|klasörünün|klasorunun)?\s*'
            r'(?:adını|adini)\s+(?P<name>[^\\/:*?"<>|]+?)\s+(?:olarak\s+)?(?:değiştir|degistir|yeniden adlandır|yeniden adlandir)\s*$',
            raw,
            flags=re.IGNORECASE,
        )
        if match:
            return ParsedFileCommand(action=action, source=_clean(match.group("src")), new_name=_clean(match.group("name")))
    return ParsedFileCommand(action=action)
