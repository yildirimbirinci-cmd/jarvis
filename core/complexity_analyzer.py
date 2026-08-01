"""Static Python complexity analysis for refactoring suggestions."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from artmach_assistant.core.workspace import WorkspaceError

_MAX_FILES = 12_000
_MAX_RESULTS = 10_000
_MAX_SOURCE_CHARS = 2_000_000
_MAX_FAILURES = 1_000
_MAX_TEXT = 20_000


def _safe_text(value: Any, *, limit: int = _MAX_TEXT) -> str:
    try:
        text = str(value)
    except BaseException:
        return ""
    return text.replace("\x00", "")[:limit]


def _threshold(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} tam sayı olmalıdır.")
    if value < 1:
        raise ValueError(f"{name} en az 1 olmalıdır.")
    if value > 1_000_000:
        raise ValueError(f"{name} çok büyük.")
    return value


@dataclass(frozen=True, slots=True)
class ComplexityThresholds:
    cyclomatic_warning: int = 11
    cognitive_warning: int = 16
    nesting_warning: int = 5
    function_lines_warning: int = 61

    def __post_init__(self) -> None:
        for name, value in (
            ("cyclomatic_warning", self.cyclomatic_warning),
            ("cognitive_warning", self.cognitive_warning),
            ("nesting_warning", self.nesting_warning),
            ("function_lines_warning", self.function_lines_warning),
        ):
            _threshold(value, name)


@dataclass(frozen=True, slots=True)
class ComplexityItem:
    path: str
    qualified_name: str
    kind: str
    line: int
    end_line: int
    line_count: int
    cyclomatic: int
    cognitive: int
    max_nesting: int
    parameter_count: int
    risk: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComplexityReport:
    root: str
    items: tuple[ComplexityItem, ...]
    files_scanned: int
    parse_failures: tuple[str, ...] = ()

    @property
    def high_risk(self) -> tuple[ComplexityItem, ...]:
        return tuple(item for item in self.items if item.risk == "high")

    @property
    def warning_count(self) -> int:
        return sum(item.risk in {"medium", "high"} for item in self.items)


class _FunctionMetricVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.cyclomatic = 1
        self.cognitive = 0
        self.max_nesting = 0
        self._nesting = 0

    def _decision(self, node: ast.AST, *, extra_paths: int = 0) -> None:
        self.cyclomatic += 1 + max(0, extra_paths)
        self.cognitive += 1 + self._nesting
        self.max_nesting = max(self.max_nesting, self._nesting + 1)
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    def visit_If(self, node: ast.If) -> None: self._decision(node)
    def visit_For(self, node: ast.For) -> None: self._decision(node)
    def visit_AsyncFor(self, node: ast.AsyncFor) -> None: self._decision(node)
    def visit_While(self, node: ast.While) -> None: self._decision(node)
    def visit_With(self, node: ast.With) -> None: self._decision(node)
    def visit_AsyncWith(self, node: ast.AsyncWith) -> None: self._decision(node)
    def visit_IfExp(self, node: ast.IfExp) -> None: self._decision(node)

    def visit_Try(self, node: ast.Try) -> None:
        paths = len(node.handlers) + bool(node.orelse) + bool(node.finalbody)
        self._decision(node, extra_paths=max(0, paths - 1))

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self.visit_Try(node)  # type: ignore[arg-type]

    def visit_Match(self, node: ast.Match) -> None:
        self.cyclomatic += max(1, len(node.cases))
        self.cognitive += 1 + self._nesting
        self.max_nesting = max(self.max_nesting, self._nesting + 1)
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        additions = max(0, len(node.values) - 1)
        self.cyclomatic += additions
        self.cognitive += additions
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.cyclomatic += 1 + len(node.ifs)
        self.cognitive += 1 + self._nesting + len(node.ifs)
        self.max_nesting = max(self.max_nesting, self._nesting + 1)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.cyclomatic += 1
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None: return
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None: return
    def visit_Lambda(self, node: ast.Lambda) -> None: return


class ComplexityAnalyzer:
    def __init__(self, workspace: object, thresholds: ComplexityThresholds | None = None) -> None:
        self._workspace = workspace
        self._thresholds = thresholds or ComplexityThresholds()

    def analyze(
        self,
        paths: Iterable[str | Path] | None = None,
        *,
        include_low_risk: bool = True,
        limit: int = 500,
    ) -> ComplexityReport:
        if not isinstance(include_low_risk, bool):
            raise WorkspaceError("include_low_risk boolean olmalıdır.")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise WorkspaceError("Karmaşıklık sonuç limiti en az 1 olan tam sayı olmalıdır.")
        limit = min(limit, _MAX_RESULTS)
        root = Path(self._workspace.require_root()).resolve(strict=False)
        candidates = self._resolve_paths(root, paths)
        items: list[ComplexityItem] = []
        failures: list[str] = []
        scanned = 0
        for relative in candidates[:_MAX_FILES]:
            try:
                source = self._workspace.read_text(relative, max_chars=_MAX_SOURCE_CHARS + 1)
                if not isinstance(source, str) or len(source) > _MAX_SOURCE_CHARS:
                    raise WorkspaceError("Kaynak dosya analiz sınırını aşıyor.")
                tree = ast.parse(source, filename=relative)
            except (OSError, UnicodeError, SyntaxError, WorkspaceError, ValueError, MemoryError, RecursionError) as exc:
                if len(failures) < _MAX_FAILURES:
                    failures.append(f"{_safe_text(relative, limit=2048)}: {_safe_text(exc)}")
                continue
            scanned += 1
            items.extend(self._items_for_file(relative, tree))
            if len(items) > _MAX_RESULTS * 4:
                items = items[:_MAX_RESULTS * 4]
        if not include_low_risk:
            items = [item for item in items if item.risk != "low"]
        items.sort(key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(item.risk, 3),
            -item.cognitive,
            -item.cyclomatic,
            item.path.casefold(),
            item.line,
            item.qualified_name.casefold(),
        ))
        return ComplexityReport(
            root=str(root),
            items=tuple(items[:limit]),
            files_scanned=scanned,
            parse_failures=tuple(sorted(failures, key=str.casefold)),
        )

    def analyze_content(self, path: str, source: str) -> tuple[ComplexityItem, ...]:
        path_text = _safe_text(path, limit=2048).strip()
        if not path_text:
            raise WorkspaceError("Dosya yolu boş olamaz.")
        if not isinstance(source, str):
            raise WorkspaceError("Kaynak metin string olmalıdır.")
        if len(source) > _MAX_SOURCE_CHARS:
            raise WorkspaceError("Kaynak dosya analiz sınırını aşıyor.")
        try:
            tree = ast.parse(source, filename=path_text)
        except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
            line = getattr(exc, "lineno", 0) or 0
            message = _safe_text(getattr(exc, "msg", exc))
            raise WorkspaceError(f"Geçersiz Python dosyası: {path_text}:{line}: {message}") from exc
        return tuple(self._items_for_file(path_text, tree))

    def _resolve_paths(self, root: Path, paths: Iterable[str | Path] | None) -> tuple[str, ...]:
        if paths is None:
            found: list[str] = []
            try:
                iterator = root.rglob("*.py")
                for path in iterator:
                    if len(found) >= _MAX_FILES:
                        break
                    try:
                        if path.is_symlink() or not path.is_file():
                            continue
                    except OSError:
                        continue
                    if any(part in {".git", "venv", ".venv", "__pycache__"} for part in path.parts):
                        continue
                    found.append(path.relative_to(root).as_posix())
            except OSError:
                pass
            return tuple(sorted(found, key=str.casefold))
        result: list[str] = []
        try:
            iterator = iter(paths)
        except TypeError as exc:
            raise WorkspaceError("Analiz yolları iterable olmalıdır.") from exc
        while len(result) < _MAX_FILES:
            try:
                raw = next(iterator)
            except StopIteration:
                break
            except BaseException as exc:
                raise WorkspaceError(f"Analiz yolları okunamadı: {_safe_text(exc)}") from exc
            text = _safe_text(raw, limit=2048).strip()
            if not text:
                continue
            safe = Path(self._workspace.safe_path(text)).resolve(strict=False)
            try:
                relative = safe.relative_to(root).as_posix()
            except ValueError as exc:
                raise WorkspaceError(f"Proje dışındaki dosya analiz edilemez: {text}") from exc
            try:
                if safe.is_symlink():
                    continue
                is_dir = safe.is_dir()
            except OSError:
                continue
            if is_dir:
                try:
                    for child in safe.rglob("*.py"):
                        if len(result) >= _MAX_FILES:
                            break
                        try:
                            if child.is_symlink() or not child.is_file() or "__pycache__" in child.parts:
                                continue
                        except OSError:
                            continue
                        result.append(child.relative_to(root).as_posix())
                except OSError:
                    continue
            elif safe.suffix.casefold() == ".py":
                result.append(relative)
        return tuple(sorted(dict.fromkeys(result), key=str.casefold))

    def _items_for_file(self, path: str, tree: ast.Module) -> list[ComplexityItem]:
        items: list[ComplexityItem] = []
        def walk(body: list[ast.stmt], prefix: str = "") -> None:
            for node in body:
                if len(items) >= _MAX_RESULTS * 4:
                    return
                if isinstance(node, ast.ClassDef):
                    qualified = f"{prefix}.{node.name}" if prefix else node.name
                    before = len(items)
                    walk(node.body, qualified)
                    methods = items[before:]
                    if methods:
                        reasons = []
                        if max(item.cognitive for item in methods) >= self._thresholds.cognitive_warning:
                            reasons.append("sınıf yüksek karmaşıklıklı metot içeriyor")
                        end_line = int(getattr(node, "end_lineno", node.lineno))
                        items.append(ComplexityItem(
                            path=path, qualified_name=qualified, kind="class",
                            line=int(node.lineno), end_line=end_line,
                            line_count=end_line - int(node.lineno) + 1,
                            cyclomatic=sum(item.cyclomatic for item in methods),
                            cognitive=sum(item.cognitive for item in methods),
                            max_nesting=max(item.max_nesting for item in methods),
                            parameter_count=0,
                            risk="medium" if reasons else "low",
                            reasons=tuple(reasons),
                        ))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = f"{prefix}.{node.name}" if prefix else node.name
                    items.append(self._function_item(path, qualified, node, bool(prefix)))
        walk(tree.body)
        return items

    def _function_item(self, path: str, qualified: str, node: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool) -> ComplexityItem:
        visitor = _FunctionMetricVisitor()
        for statement in node.body:
            visitor.visit(statement)
        end_line = int(getattr(node, "end_lineno", node.lineno))
        line_count = end_line - int(node.lineno) + 1
        parameter_count = (
            len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
            + int(node.args.vararg is not None) + int(node.args.kwarg is not None)
        )
        if is_method and node.args.args and node.args.args[0].arg in {"self", "cls"}:
            parameter_count -= 1
        reasons = []
        if visitor.cyclomatic >= self._thresholds.cyclomatic_warning: reasons.append(f"cyclomatic={visitor.cyclomatic}")
        if visitor.cognitive >= self._thresholds.cognitive_warning: reasons.append(f"cognitive={visitor.cognitive}")
        if visitor.max_nesting >= self._thresholds.nesting_warning: reasons.append(f"nesting={visitor.max_nesting}")
        if line_count >= self._thresholds.function_lines_warning: reasons.append(f"lines={line_count}")
        score = sum((
            visitor.cyclomatic >= self._thresholds.cyclomatic_warning,
            visitor.cognitive >= self._thresholds.cognitive_warning,
            visitor.max_nesting >= self._thresholds.nesting_warning,
            line_count >= self._thresholds.function_lines_warning,
        ))
        return ComplexityItem(
            path=path, qualified_name=qualified, kind="method" if is_method else "function",
            line=int(node.lineno), end_line=end_line, line_count=line_count,
            cyclomatic=visitor.cyclomatic, cognitive=visitor.cognitive,
            max_nesting=visitor.max_nesting, parameter_count=max(0, parameter_count),
            risk="high" if score >= 2 else "medium" if score == 1 else "low",
            reasons=tuple(reasons),
        )
