from __future__ import annotations

import difflib
from dataclasses import dataclass


_NO_FINAL_NEWLINE = "\\ No newline at end of file"


@dataclass(frozen=True, slots=True)
class DiffRow:
    old_no: str
    old_text: str
    new_no: str
    new_text: str
    kind: str


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def side_by_side(old: str, new: str) -> list[DiffRow]:
    """Build a line-oriented diff while preserving final-newline changes."""
    old_text = _require_text(old, name="old")
    new_text = _require_text(new, name="new")

    rows: list[DiffRow] = []
    old_no = 0
    new_no = 0
    for line in difflib.ndiff(old_text.splitlines(), new_text.splitlines()):
        code = line[:2]
        text = line[2:]
        if code == "  ":
            old_no += 1
            new_no += 1
            rows.append(DiffRow(str(old_no), text, str(new_no), text, "same"))
        elif code == "- ":
            old_no += 1
            rows.append(DiffRow(str(old_no), text, "", "", "delete"))
        elif code == "+ ":
            new_no += 1
            rows.append(DiffRow("", "", str(new_no), text, "add"))

    old_has_final_newline = old_text.endswith(("\n", "\r"))
    new_has_final_newline = new_text.endswith(("\n", "\r"))
    if old_has_final_newline != new_has_final_newline:
        if old_has_final_newline:
            rows.append(DiffRow("", "", "", _NO_FINAL_NEWLINE, "add"))
        else:
            rows.append(DiffRow("", _NO_FINAL_NEWLINE, "", "", "delete"))

    return rows
