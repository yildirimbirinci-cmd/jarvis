from pathlib import Path
import ast
import copy
import re
from typing import Any


_SYMBOL_PATTERN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b"
)


def _requested_symbol(instruction: str) -> tuple[str, str] | None:
    match = _SYMBOL_PATTERN.search(str(instruction or ""))
    if not match:
        return None
    return match.group(1), match.group(2)


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
