"""Post-refactoring regression checks with optional automatic rollback guidance."""
from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Protocol

from artmach_assistant.core.path_normalizer import path_key
try:
    from artmach_assistant.core.workspace import WorkspaceError
except ModuleNotFoundError:  # Lightweight/isolated test environments.
    class WorkspaceError(RuntimeError):
        pass


class RegressionSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class RegressionIssue:
    severity: RegressionSeverity
    code: str
    message: str
    path: str = ""
    line: int | None = None


@dataclass(frozen=True, slots=True)
class RegressionReport:
    checked_files: tuple[str, ...]
    issues: tuple[RegressionIssue, ...]

    @property
    def errors(self) -> tuple[RegressionIssue, ...]:
        return tuple(i for i in self.issues if i.severity is RegressionSeverity.ERROR)

    @property
    def warnings(self) -> tuple[RegressionIssue, ...]:
        return tuple(i for i in self.issues if i.severity is RegressionSeverity.WARNING)

    @property
    def safe(self) -> bool:
        return not self.errors

    @property
    def rollback_required(self) -> bool:
        return bool(self.errors)


class ImportResolverProtocol(Protocol):
    def validate_file(self, root: Path, path: Path) -> Iterable[object]: ...


class RegressionSafetyCheck:
    """Validate changed Python files without mutating the workspace."""

    def __init__(self, import_resolver: ImportResolverProtocol | None = None) -> None:
        self._import_resolver = import_resolver

    def check(
        self,
        root: str | Path,
        changed_paths: Iterable[str],
        *,
        expected_symbols: dict[str, Iterable[str]] | None = None,
    ) -> RegressionReport:
        project_root = Path(root).expanduser().resolve()
        if not project_root.is_dir():
            raise WorkspaceError("Regression kontrolü için geçerli proje kökü gerekli.")

        issues: list[RegressionIssue] = []
        checked: list[str] = []
        seen: set[str] = set()
        if expected_symbols is None:
            expected_symbols = {}
        elif not hasattr(expected_symbols, "get"):
            raise TypeError("expected_symbols must be a mapping")

        if isinstance(changed_paths, (str, Path)):
            path_rows: Iterable[object] = (changed_paths,)
        else:
            try:
                path_rows = tuple(changed_paths)
            except (TypeError, RuntimeError) as exc:
                raise TypeError("changed_paths must be an iterable of paths") from exc

        for raw in path_rows:
            relative = str(raw or "").strip().replace("\\", "/")
            if not relative:
                continue
            try:
                target = (project_root / relative).resolve()
                canonical = target.relative_to(project_root).as_posix()
            except (OSError, RuntimeError, ValueError):
                issues.append(RegressionIssue(
                    RegressionSeverity.ERROR,
                    "outside_workspace",
                    "Değişen dosya proje kökünün dışında.",
                    relative,
                ))
                continue
            key = path_key(target)
            if key in seen:
                continue
            seen.add(key)
            checked.append(canonical)
            if not target.exists():
                issues.append(RegressionIssue(
                    RegressionSeverity.ERROR,
                    "missing_file",
                    "Refactoring sonrasında beklenen dosya bulunamadı.",
                    canonical,
                ))
                continue
            if not target.is_file() or target.suffix.casefold() != ".py":
                continue
            source: str | None = None
            try:
                # Respect PEP 263 encoding declarations instead of assuming UTF-8.
                # Refactoring validation must be able to inspect every Python source
                # file that the interpreter itself can import.
                with tokenize.open(target) as handle:
                    source = handle.read()
                tree = ast.parse(source, filename=canonical)
            except OSError as exc:
                issues.append(RegressionIssue(
                    RegressionSeverity.ERROR,
                    "read_error",
                    f"Dosya okunamadı: {exc}",
                    canonical,
                ))
                continue
            except (UnicodeDecodeError, SyntaxError) as exc:
                # ``tokenize.open`` may raise SyntaxError for an invalid or
                # unknown encoding cookie. Keep that distinct from parser
                # syntax failures whenever no source text could be decoded.
                if isinstance(exc, UnicodeDecodeError) or source is None:
                    issues.append(RegressionIssue(
                        RegressionSeverity.ERROR,
                        "decode_error",
                        f"Dosya kodlaması çözülemedi: {exc}",
                        canonical,
                        getattr(exc, "lineno", None),
                    ))
                    continue
                issues.append(RegressionIssue(
                    RegressionSeverity.ERROR,
                    "syntax_error",
                    exc.msg,
                    canonical,
                    exc.lineno,
                ))
                continue
            except (RecursionError, MemoryError) as exc:
                issues.append(RegressionIssue(
                    RegressionSeverity.ERROR,
                    "parser_limit",
                    f"Python ayrıştırıcısı güvenli sınırı aştı: {type(exc).__name__}",
                    canonical,
                ))
                continue

            actual = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
            symbol_rows = expected_symbols.get(canonical, ())
            if isinstance(symbol_rows, str):
                symbol_rows = (symbol_rows,)
            try:
                symbols = tuple(symbol_rows)
            except (TypeError, RuntimeError):
                issues.append(RegressionIssue(
                    RegressionSeverity.ERROR,
                    "invalid_expected_symbols",
                    "Beklenen sembol listesi okunamadı.",
                    canonical,
                ))
                symbols = ()
            for symbol in symbols:
                name = str(symbol or "").strip()
                if name and name not in actual:
                    issues.append(RegressionIssue(
                        RegressionSeverity.ERROR,
                        "missing_symbol",
                        f"Beklenen üst seviye sembol kayboldu: {name}",
                        canonical,
                    ))

            if self._import_resolver is not None:
                try:
                    external_issues = tuple(self._import_resolver.validate_file(project_root, target))
                except Exception as exc:
                    issues.append(RegressionIssue(
                        RegressionSeverity.WARNING,
                        "import_check_unavailable",
                        f"Import doğrulaması tamamlanamadı: {exc}",
                        canonical,
                    ))
                else:
                    for item in external_issues:
                        message = str(getattr(item, "message", item)).strip()
                        if message:
                            issues.append(RegressionIssue(
                                RegressionSeverity.ERROR,
                                "broken_import",
                                message,
                                canonical,
                                getattr(item, "line", None),
                            ))

        issues.sort(key=lambda i: (i.severity.value, i.path, i.line or 0, i.code, i.message))
        return RegressionReport(tuple(checked), tuple(issues))
