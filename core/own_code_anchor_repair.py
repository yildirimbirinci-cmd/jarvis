from pathlib import Path
import ast
import copy
import re
from typing import Any


_SYMBOL_PATTERN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b"
)


def _requested_symbol(instruction: str) -> tuple[str, str] | None:
    """Return an explicit Class.method reference, ignoring file names."""
    text = str(instruction or "")
    ignored_suffixes = {
        "py", "pyw", "json", "toml", "yaml", "yml", "md", "txt",
        "ini", "cfg", "bat", "cmd", "ps1", "sh", "html", "css",
    }

    matches = list(_SYMBOL_PATTERN.finditer(text))

    # Prefer the conventional Class.method form.
    for match in matches:
        owner = match.group(1)
        member = match.group(2)

        if member.casefold() in ignored_suffixes:
            continue
        if owner[:1].isupper():
            return owner, member

    # Fall back to any non-file dotted symbol.
    for match in matches:
        owner = match.group(1)
        member = match.group(2)

        if member.casefold() not in ignored_suffixes:
            return owner, member

    return None


def _symbol_source(
    source: str,
    *,
    class_name: str,
    method_name: str,
) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    lines = source.splitlines(keepends=True)

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue

        for child in node.body:
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if child.name != method_name:
                continue

            start = max(0, int(child.lineno) - 1)
            end = int(getattr(child, "end_lineno", child.lineno))
            return "".join(lines[start:end])

    return ""


def _expand_unique_replace(
    source: str,
    symbol_source: str,
    old: str,
    new: str,
) -> tuple[str, str] | None:
    if not old or source.count(old) <= 1:
        return None

    if symbol_source.count(old) != 1:
        return None

    position = symbol_source.find(old)
    if position < 0:
        return None

    before = symbol_source[:position]
    after = symbol_source[position + len(old):]

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    for radius in range(1, 13):
        prefix = "".join(before_lines[-radius:])
        suffix = "".join(after_lines[:radius])

        expanded_old = prefix + old + suffix
        if source.count(expanded_old) != 1:
            continue

        expanded_new = prefix + new + suffix
        return expanded_old, expanded_new

    return None


def repair_ambiguous_replace_anchors(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> dict[str, Any]:
    """Safely expand ambiguous replace anchors inside an explicit symbol.

    Only replace operations are changed. Missing anchors and operations outside
    the explicitly named Class.method scope remain untouched and are rejected
    later by the normal EditManager validation.
    """

    requested = _requested_symbol(instruction)
    if requested is None:
        return payload

    class_name, method_name = requested
    repaired = copy.deepcopy(payload)

    files = repaired.get("files")
    if not isinstance(files, list):
        return repaired

    root = Path(project_root).resolve(strict=False)

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        raw_path = str(file_row.get("path", "")).strip()
        if not raw_path:
            continue

        candidate = (root / raw_path).resolve(strict=False)

        try:
            candidate.relative_to(root)
        except ValueError:
            continue

        if not candidate.is_file():
            continue

        try:
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        scoped_source = _symbol_source(
            source,
            class_name=class_name,
            method_name=method_name,
        )
        if not scoped_source:
            continue

        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue

        for operation in operations:
            if not isinstance(operation, dict):
                continue
            if str(operation.get("op", "")).strip().casefold() != "replace":
                continue

            old = operation.get("old")
            new = operation.get("new")

            if not isinstance(old, str) or not isinstance(new, str):
                continue

            expanded = _expand_unique_replace(
                source,
                scoped_source,
                old,
                new,
            )
            if expanded is None:
                continue

            operation["old"], operation["new"] = expanded

    return repaired


def _occurrence_positions(text: str, fragment: str) -> tuple[int, ...]:
    if not fragment:
        return ()

    positions: list[int] = []
    start = 0

    while True:
        position = text.find(fragment, start)
        if position < 0:
            break
        positions.append(position)
        start = position + max(1, len(fragment))

    return tuple(positions)


def _unique_context_for_occurrence(
    source: str,
    scoped_source: str,
    fragment: str,
    position: int,
) -> str:
    before = scoped_source[:position]
    after = scoped_source[position + len(fragment):]

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    for radius in range(1, 17):
        prefix = "".join(before_lines[-radius:])
        suffix = "".join(after_lines[:radius])
        candidate = prefix + fragment + suffix

        if source.count(candidate) == 1:
            return candidate

    return ""


def build_ambiguous_anchor_guidance(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
    limit: int = 6,
) -> str:
    """Describe exact unique candidates for ambiguous replace anchors.

    This function never chooses or changes an operation. It only returns
    source-derived alternatives so the model can select the intended block.
    """

    requested = _requested_symbol(instruction)
    if requested is None:
        return ""

    class_name, method_name = requested
    root = Path(project_root).resolve(strict=False)

    try:
        maximum = max(1, min(int(limit), 12))
    except (TypeError, ValueError, OverflowError):
        maximum = 6

    rows: list[str] = []
    files = payload.get("files")

    if not isinstance(files, list):
        return ""

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        raw_path = str(file_row.get("path", "")).strip()
        if not raw_path:
            continue

        candidate = (root / raw_path).resolve(strict=False)

        try:
            candidate.relative_to(root)
        except ValueError:
            continue

        if not candidate.is_file():
            continue

        try:
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        scoped_source = _symbol_source(
            source,
            class_name=class_name,
            method_name=method_name,
        )
        if not scoped_source:
            continue

        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue

        for operation_index, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                continue
            if str(operation.get("op", "")).strip().casefold() != "replace":
                continue

            old = operation.get("old")
            if not isinstance(old, str) or not old:
                continue

            positions = _occurrence_positions(scoped_source, old)
            if len(positions) <= 1:
                continue

            candidates: list[str] = []

            for position in positions:
                expanded = _unique_context_for_occurrence(
                    source,
                    scoped_source,
                    old,
                    position,
                )
                if expanded and expanded not in candidates:
                    candidates.append(expanded)

                if len(candidates) >= maximum:
                    break

            if not candidates:
                continue

            rows.extend(
                (
                    "",
                    f"BELIRSIZ ANCHOR REHBERI: {raw_path} i?lem {operation_index}",
                    (
                        f"Ayn? old metni {len(positions)} kez bulundu. "
                        "A?a??daki adaylardan yaln?zca ama?lanan blo?u birebir "
                        "old alan? olarak kullan."
                    ),
                )
            )

            for number, expanded in enumerate(candidates, start=1):
                rows.append(f"\nADAY {number}:\n{expanded}")

    if not rows:
        return ""

    return "\n".join(rows).strip()


def merge_duplicate_operation_rows(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge repeated operation-only rows that target the same file.

    Full-content records and mixed content/operations records are left intact
    so EditManager can reject unsafe or ambiguous payloads normally.
    """

    repaired = copy.deepcopy(payload)
    files = repaired.get("files")

    if not isinstance(files, list):
        return repaired

    merged: list[Any] = []
    operation_rows: dict[str, dict[str, Any]] = {}

    for row in files:
        if not isinstance(row, dict):
            merged.append(row)
            continue

        raw_path = str(row.get("path", "")).strip().replace("\\", "/")
        while raw_path.startswith("./"):
            raw_path = raw_path[2:]

        operations = row.get("operations")
        content = row.get("content")

        if (
            not raw_path
            or not isinstance(operations, list)
            or isinstance(content, str)
        ):
            merged.append(row)
            continue

        key = raw_path.casefold()
        existing = operation_rows.get(key)

        if existing is None:
            copied = copy.deepcopy(row)
            copied["path"] = raw_path
            operation_rows[key] = copied
            merged.append(copied)
            continue

        existing_operations = existing.get("operations")
        if not isinstance(existing_operations, list):
            merged.append(row)
            continue

        existing_operations.extend(copy.deepcopy(operations))

        old_reason = str(existing.get("reason", "")).strip()
        new_reason = str(row.get("reason", "")).strip()

        reasons = [
            value
            for value in (old_reason, new_reason)
            if value
        ]
        existing["reason"] = " | ".join(dict.fromkeys(reasons))

    repaired["files"] = merged
    return repaired

