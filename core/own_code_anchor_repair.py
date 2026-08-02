from pathlib import Path
import ast
import copy
import difflib
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


def remove_redundant_noop_replaces(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop literal ``old == new`` rows when real operations remain.

    Removing such a row cannot alter the rendered file. If a file contains
    only no-ops, preserve them so the normal validator still rejects the
    proposal instead of silently accepting an empty change.
    """

    repaired = copy.deepcopy(payload)
    files = repaired.get("files")
    if not isinstance(files, list):
        return repaired

    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        operations = file_row.get("operations")
        if not isinstance(operations, list) or len(operations) < 2:
            continue

        retained: list[Any] = []
        removed = False
        for operation in operations:
            is_literal_noop = (
                isinstance(operation, dict)
                and str(operation.get("op", "replace")).strip().casefold()
                in {"replace", "replace_exact"}
                and isinstance(operation.get("old"), str)
                and operation.get("old") == operation.get("new")
            )
            if is_literal_noop:
                removed = True
                continue
            retained.append(operation)

        if removed and retained:
            file_row["operations"] = retained

    return repaired


def _normalise_anchor_lines(value: str) -> tuple[str, ...]:
    """Normalize indentation without changing token or line ordering."""
    rows = str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()

    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()

    return tuple(" ".join(row.strip().split()) for row in rows)


def _unique_whitespace_match(
    scoped_source: str,
    requested: str,
) -> str:
    requested_rows = _normalise_anchor_lines(requested)

    if not requested_rows:
        return ""

    source_lines = scoped_source.splitlines(keepends=True)
    window_size = len(requested_rows)
    matched_window = ""
    match_count = 0

    for index in range(0, len(source_lines) - window_size + 1):
        window = "".join(source_lines[index:index + window_size])

        if _normalise_anchor_lines(window) != requested_rows:
            continue

        match_count += 1

        if match_count > 1:
            return ""

        matched_window = window

    return matched_window if match_count == 1 else ""


def repair_unique_whitespace_anchors(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> dict[str, Any]:
    """Replace whitespace-variant anchors with one exact source fragment.

    The repair is applied only inside an explicitly requested Class.method.
    Zero or multiple normalized matches are left untouched for EditManager to
    reject normally.
    """

    requested_symbol = _requested_symbol(instruction)
    if requested_symbol is None:
        return payload

    class_name, method_name = requested_symbol
    root = Path(project_root).resolve(strict=False)
    repaired = copy.deepcopy(payload)
    files = repaired.get("files")

    if not isinstance(files, list):
        return repaired

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        raw_path = str(file_row.get("path", "")).strip().replace("\\", "/")
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

            operation_name = str(
                operation.get("op", "")
            ).strip().casefold()

            anchor_field = (
                "anchor"
                if operation_name in {"insert_before", "insert_after"}
                else "old"
                if operation_name in {"replace", "delete"}
                else ""
            )

            if not anchor_field:
                continue

            requested_anchor = operation.get(anchor_field)
            if not isinstance(requested_anchor, str) or not requested_anchor:
                continue

            # Exact matches are already valid or handled by ambiguity repair.
            if source.count(requested_anchor) != 0:
                continue

            exact_source_anchor = _unique_whitespace_match(
                scoped_source,
                requested_anchor,
            )
            if not exact_source_anchor:
                continue

            operation[anchor_field] = exact_source_anchor

    return repaired


def _normalised_similarity_text(value: str) -> str:
    rows = _normalise_anchor_lines(value)
    return "\n".join(rows)


def _closest_unique_source_window(
    scoped_source: str,
    requested: str,
) -> tuple[str, float]:
    """Find one strong source excerpt even when line counts differ.

    Overlapping candidates around the same source block are treated as one
    region. A similarly strong candidate from another region makes the result
    ambiguous and therefore unusable as retry guidance.
    """

    requested_rows = _normalise_anchor_lines(requested)
    if not requested_rows:
        return "", 0.0

    source_lines = scoped_source.splitlines(keepends=True)
    if not source_lines:
        return "", 0.0

    requested_text = "\n".join(requested_rows)
    requested_size = len(requested_rows)

    size_delta = max(6, requested_size // 2)
    minimum_size = max(1, requested_size - size_delta)
    maximum_size = min(
        len(source_lines),
        requested_size + size_delta,
    )

    scored: list[tuple[float, int, int, str]] = []

    for window_size in range(minimum_size, maximum_size + 1):
        for index in range(
            0,
            len(source_lines) - window_size + 1,
        ):
            end_index = index + window_size
            window = "".join(source_lines[index:end_index])
            candidate_text = _normalised_similarity_text(window)

            similarity = difflib.SequenceMatcher(
                None,
                requested_text,
                candidate_text,
            ).ratio()

            length_ratio = (
                min(requested_size, window_size)
                / max(requested_size, window_size)
            )

            # Prefer textually similar windows whose size is also reasonably
            # close to the rejected anchor, without requiring equal lengths.
            score = (similarity * 0.90) + (length_ratio * 0.10)

            scored.append(
                (
                    score,
                    index,
                    end_index,
                    window,
                )
            )

    if not scored:
        return "", 0.0

    scored.sort(key=lambda item: item[0], reverse=True)

    best_score, best_start, best_end, best_window = scored[0]

    # This is guidance, not an automatic edit, but still require a meaningful
    # structural resemblance before exposing a source block to the next retry.
    if best_score < 0.62:
        return "", best_score

    for challenger_score, challenger_start, challenger_end, _ in scored[1:]:
        overlaps_best = not (
            challenger_end <= best_start
            or challenger_start >= best_end
        )

        if overlaps_best:
            continue

        # A similarly strong candidate in another source region means the
        # proposed anchor cannot be mapped safely to one exact block.
        if challenger_score >= best_score - 0.035:
            return "", best_score

        # Remaining candidates are sorted lower and cannot become ambiguous.
        break

    return best_window, best_score

def build_missing_anchor_guidance(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> str:
    """Return exact source candidates for zero-match operations.

    This function only provides retry guidance. It never changes the proposal.
    """

    requested_symbol = _requested_symbol(instruction)
    if requested_symbol is None:
        return ""

    class_name, method_name = requested_symbol
    root = Path(project_root).resolve(strict=False)
    rows: list[str] = []

    files = payload.get("files")
    if not isinstance(files, list):
        return ""

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        raw_path = str(file_row.get("path", "")).strip().replace("\\", "/")
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

            operation_name = str(
                operation.get("op", "")
            ).strip().casefold()

            field = (
                "anchor"
                if operation_name in {"insert_before", "insert_after"}
                else "old"
                if operation_name in {"replace", "delete"}
                else ""
            )

            if not field:
                continue

            requested_anchor = operation.get(field)
            if not isinstance(requested_anchor, str) or not requested_anchor:
                continue

            if source.count(requested_anchor) != 0:
                continue

            closest, score = _closest_unique_source_window(
                scoped_source,
                requested_anchor,
            )
            if not closest:
                continue

            rows.extend(
                (
                    "",
                    (
                        f"EKS?K ANCHOR REHBER?: {raw_path} "
                        f"i?lem {operation_index}"
                    ),
                    (
                        f"Model anchor? kaynakta bulunmad?. En yak?n benzersiz "
                        f"ger?ek kaynak blo?u (benzerlik %{int(score * 100)}):"
                    ),
                    "Bu blo?u birebir kullan veya i?lemi yeniden tasarla.",
                    f"\nGER?EK KAYNAK BLO?U:\n{closest}",
                )
            )

    return "\n".join(rows).strip()
