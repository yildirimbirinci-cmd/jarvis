from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable


def _node_segment(lines: list[str], node: ast.AST) -> str:
    start = max(1, int(getattr(node, "lineno", 1)))
    end = max(start, int(getattr(node, "end_lineno", start)))
    return "".join(lines[start - 1 : end])


def _module_prelude(tree: ast.Module, lines: list[str], *, limit: int = 5000) -> str:
    pieces: list[str] = []
    used = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            segment = _node_segment(lines, node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            segment = _node_segment(lines, node)
            if len(segment) > 1200:
                continue
        else:
            continue
        if used + len(segment) > limit:
            break
        pieces.append(segment)
        used += len(segment)
    return "".join(pieces)


def _matching_nodes(tree: ast.Module, symbols: tuple[str, ...]) -> list[tuple[str, ast.AST]]:
    requested = [symbol.strip() for symbol in symbols if symbol.strip()]
    if not requested:
        return []
    results: list[tuple[str, ast.AST]] = []
    seen: set[tuple[int, int]] = set()

    for symbol in requested:
        parts = [part for part in symbol.split(".") if part]
        class_name = parts[-2] if len(parts) >= 2 else ""
        member_name = parts[-1] if parts else ""
        for node in ast.walk(tree):
            matched = False
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == member_name:
                    if class_name:
                        # Verify that the requested class actually owns this method.
                        for parent in ast.walk(tree):
                            if isinstance(parent, ast.ClassDef) and parent.name == class_name:
                                if node in parent.body:
                                    matched = True
                                    break
                    else:
                        matched = True
            elif isinstance(node, ast.ClassDef) and node.name == member_name:
                matched = True
            if not matched:
                continue
            key = (int(getattr(node, "lineno", 0)), int(getattr(node, "end_lineno", 0)))
            if key in seen:
                continue
            seen.add(key)
            results.append((symbol, node))
    results.sort(key=lambda item: int(getattr(item[1], "lineno", 0)))
    return results


def build_symbol_context(
    path: str | Path,
    symbols: Iterable[str] = (),
    *,
    max_chars: int = 28000,
) -> str:
    """Return bounded source context centered on approved Python symbols.

    The model receives imports and the complete target function/class instead of
    the first N characters of a large module.  If symbol resolution fails, the
    function falls back to a bounded full-file excerpt and labels that fallback.
    """

    source_path = Path(path)
    content = source_path.read_text(encoding="utf-8", errors="replace")
    limit = max(2000, min(int(max_chars), 120000))
    symbol_rows = tuple(dict.fromkeys(str(item).strip() for item in symbols if str(item).strip()))
    if source_path.suffix.casefold() != ".py" or not symbol_rows:
        return content[:limit]
    try:
        tree = ast.parse(content, filename=str(source_path))
    except SyntaxError:
        return content[:limit]
    lines = content.splitlines(keepends=True)
    matches = _matching_nodes(tree, symbol_rows)
    if not matches:
        return "SEMBOL BULUNAMADI; SINIRLI DOSYA BAGLAMI:\n" + content[:limit]

    sections: list[str] = []
    prelude = _module_prelude(tree, lines)
    if prelude:
        sections.append("MODUL IMPORT VE SABITLERI:\n" + prelude)
    remaining = limit - sum(len(item) for item in sections)
    for symbol, node in matches:
        segment = _node_segment(lines, node)
        header = (
            f"HEDEF SEMBOL: {symbol} | SATIR "
            f"{getattr(node, 'lineno', '?')}-{getattr(node, 'end_lineno', '?')}\n"
        )
        if len(header) + len(segment) > remaining:
            segment = segment[: max(0, remaining - len(header))]
        sections.append(header + segment)
        remaining = limit - sum(len(item) for item in sections)
        if remaining <= 200:
            break
    return "\n\n".join(sections)[:limit]
