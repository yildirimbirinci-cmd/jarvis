from __future__ import annotations

import ast
import io
import re
import tokenize
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.project_index import IGNORED_DIRS
from artmach_assistant.core.workspace import WorkspaceService


_IGNORED_PARTS = set(IGNORED_DIRS) | {
    ".artmach_assistant",
}
_SUPPORTED_SUFFIXES = {".py", ".cpp", ".cc", ".c", ".h", ".hpp", ".qml", ".js", ".ts"}
_MAX_FILES = 20_000
_MAX_FILE_BYTES = 4_000_000
_MAX_ISSUES = 10_000
_MAX_OUTPUT_ISSUES = 300


_SEVERITY_BY_KIND = {
    "SYNTAX": "critical",
    "SECURITY": "high",
    "QUALITY": "medium",
    "COMPLEXITY": "medium",
    "DUPLICATE": "medium",
    "TODO": "low",
    "STYLE": "low",
}


@dataclass(frozen=True, slots=True)
class CodeReviewIssue:
    kind: str
    path: str
    line: int
    message: str
    severity: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}" if self.line > 0 else self.path


@dataclass(frozen=True, slots=True)
class CodeReviewAnalysis:
    root: str
    scanned_files: int
    issues: tuple[CodeReviewIssue, ...]
    scan_limit_reached: bool = False
    issue_limit_reached: bool = False

    @property
    def counts(self) -> Counter[str]:
        return Counter(issue.kind for issue in self.issues)

    @property
    def has_findings(self) -> bool:
        return bool(self.issues)

    def report(self, *, limit: int = _MAX_OUTPUT_ISSUES) -> str:
        if not self.issues:
            return "Belirgin statik kod sorunu bulunamadı."
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = _MAX_OUTPUT_ISSUES
        output_limit = max(1, min(limit, _MAX_OUTPUT_ISSUES))
        counts = self.counts
        output = [
            "KOD İNCELEME ÖZETİ",
            " | ".join(f"{kind}: {count}" for kind, count in counts.most_common()),
            "",
        ]
        output.extend(
            f"[{issue.kind}] {issue.path}:{issue.line} — {issue.message}"
            for issue in self.issues[:output_limit]
        )
        hidden = len(self.issues) - output_limit
        if hidden > 0:
            output.append(f"\n... {hidden} ek bulgu gösterilmedi.")
        if self.scan_limit_reached:
            output.append(f"\n... dosya tarama sınırına ulaşıldı ({_MAX_FILES}).")
        if self.issue_limit_reached:
            output.append(f"\n... bulgu sınırına ulaşıldı ({_MAX_ISSUES}).")
        return "\n".join(output)


class CodeReviewService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def analyze(self) -> CodeReviewAnalysis:
        """Return structured, read-only findings for reuse by architecture tools."""

        root = Path(self.workspace.require_root()).expanduser().resolve(strict=False)
        issues: list[CodeReviewIssue] = []
        signatures: Counter[tuple[str, int]] = Counter()
        signature_locations: dict[tuple[str, int], list[tuple[str, int]]] = {}
        scanned = 0
        scan_limit_reached = False

        for path in self._iter_candidates(root):
            if scanned >= _MAX_FILES:
                scan_limit_reached = True
                break
            if len(issues) >= _MAX_ISSUES:
                break
            scanned += 1
            try:
                relative_path = path.relative_to(root)
            except (TypeError, ValueError):
                continue
            rel = relative_path.as_posix()
            text = self._read_source(path)
            if text is None:
                continue
            python_source = path.suffix.casefold() == ".py"
            self._scan_lines(text, rel, issues, python_source=python_source)
            if python_source:
                self._scan_python(text, rel, signatures, signature_locations, issues)

        for (name, argc), count in signatures.items():
            if count < 4 or len(issues) >= _MAX_ISSUES:
                continue
            locations = signature_locations.get((name, argc), ())
            first_path, first_line = locations[0] if locations else ("*", 0)
            self._append_issue(
                issues,
                "DUPLICATE",
                first_path,
                first_line,
                f"{name}/{argc} imzası {count} kez tanımlanmış",
            )

        return CodeReviewAnalysis(
            root=str(root),
            scanned_files=scanned,
            issues=tuple(issues),
            scan_limit_reached=scan_limit_reached,
            issue_limit_reached=len(issues) >= _MAX_ISSUES,
        )

    def report(self) -> str:
        return self.analyze().report()

    @staticmethod
    def _append_issue(
        issues: list[CodeReviewIssue],
        kind: str,
        path: str,
        line: int,
        message: str,
    ) -> None:
        if len(issues) >= _MAX_ISSUES:
            return
        clean_kind = str(kind).strip().upper()[:64] or "QUALITY"
        clean_path = str(path).replace("\\", "/")[:2048]
        clean_message = str(message).replace("\x00", "")[:1000]
        try:
            clean_line = max(0, int(line))
        except (TypeError, ValueError, OverflowError):
            clean_line = 0
        issues.append(
            CodeReviewIssue(
                clean_kind,
                clean_path,
                clean_line,
                clean_message,
                _SEVERITY_BY_KIND.get(clean_kind, "medium"),
            )
        )

    @staticmethod
    def _iter_candidates(root: Path) -> Iterable[Path]:
        try:
            values = root.rglob("*")
            for path in values:
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    relative = path.relative_to(root)
                    if any(part in _IGNORED_PARTS for part in relative.parts[:-1]):
                        continue
                    if path.suffix.casefold() not in _SUPPORTED_SUFFIXES:
                        continue
                    yield path
                except (OSError, RuntimeError, ValueError):
                    continue
        except (OSError, RuntimeError):
            return

    @staticmethod
    def _read_source(path: Path) -> str | None:
        try:
            size = path.stat().st_size
            if size < 0 or size > _MAX_FILE_BYTES:
                return None
            with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
                return handle.read(_MAX_FILE_BYTES + 1)[:_MAX_FILE_BYTES]
        except (OSError, RuntimeError):
            return None

    @classmethod
    def _scan_lines(
        cls,
        text: str,
        rel: str,
        issues: list[CodeReviewIssue],
        *,
        python_source: bool,
    ) -> None:
        for no, line in enumerate(text.splitlines(), 1):
            if len(issues) >= _MAX_ISSUES:
                return
            stripped = line.strip()
            if len(line) > 140:
                cls._append_issue(issues, "STYLE", rel, no, "140 karakterden uzun satır")
            if not python_source and re.search(r"\b(TODO|FIXME|HACK)\b", stripped, re.I):
                cls._append_issue(issues, "TODO", rel, no, stripped[:120])
            # Python code is parsed below so strings/comments containing these
            # examples do not become false security findings.  Non-Python
            # sources retain the conservative text checks.
            if not python_source and re.search(r"\b(eval|exec)\s*\(", stripped):
                cls._append_issue(issues, "SECURITY", rel, no, "Dinamik kod çalıştırma kullanımı")
            if not python_source and re.search(r"except\s*:\s*$", stripped):
                cls._append_issue(issues, "QUALITY", rel, no, "Çıplak except bloğu")
            if not python_source and re.search(r"password\s*=\s*[\"']", stripped, re.I):
                cls._append_issue(issues, "SECURITY", rel, no, "Olası sabit parola")

    @classmethod
    def _scan_python_comments(
        cls,
        text: str,
        rel: str,
        issues: list[CodeReviewIssue],
    ) -> None:
        """Report unfinished-work markers only from Python comments.

        Literal examples, regular expressions, documentation strings and test
        fixtures must not become maintenance findings merely because they
        contain words such as TODO or FIXME.
        """

        try:
            tokens = tokenize.generate_tokens(io.StringIO(text).readline)
            for token in tokens:
                if len(issues) >= _MAX_ISSUES:
                    return
                if token.type != tokenize.COMMENT:
                    continue
                comment = token.string.strip()
                if re.search(r"\b(TODO|FIXME|HACK)\b", comment, re.I):
                    cls._append_issue(
                        issues,
                        "TODO",
                        rel,
                        int(token.start[0]),
                        comment[:120],
                    )
        except (IndentationError, SyntaxError, tokenize.TokenError, UnicodeError):
            return

    @classmethod
    def _scan_python(
        cls,
        text: str,
        rel: str,
        signatures: Counter[tuple[str, int]],
        signature_locations: dict[tuple[str, int], list[tuple[str, int]]],
        issues: list[CodeReviewIssue],
    ) -> None:
        cls._scan_python_comments(text, rel, issues)
        try:
            tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError) as exc:
            line = int(getattr(exc, "lineno", 0) or 0)
            message = str(getattr(exc, "msg", "Geçersiz Python sözdizimi"))[:500]
            cls._append_issue(issues, "SYNTAX", rel, line, message)
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                argc = len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
                key = (node.name, argc)
                signatures[key] += 1
                signature_locations.setdefault(key, []).append((rel, int(node.lineno)))
                end_line = int(getattr(node, "end_lineno", node.lineno) or node.lineno)
                if end_line - int(node.lineno) > 80 and len(issues) < _MAX_ISSUES:
                    cls._append_issue(
                        issues,
                        "COMPLEXITY",
                        rel,
                        int(node.lineno),
                        f"{node.name}: 80 satırdan uzun fonksiyon",
                    )
                continue
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"eval", "exec"}:
                    cls._append_issue(
                        issues,
                        "SECURITY",
                        rel,
                        int(getattr(node, "lineno", 0) or 0),
                        "Dinamik kod çalıştırma kullanımı",
                    )
                continue
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                cls._append_issue(
                    issues,
                    "QUALITY",
                    rel,
                    int(getattr(node, "lineno", 0) or 0),
                    "Çıplak except bloğu",
                )
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = getattr(node, "value", None)
                if not (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and value.value
                ):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    lowered = target.id.casefold()
                    if any(
                        marker in lowered
                        for marker in ("password", "passwd", "secret", "api_key", "apikey")
                    ):
                        cls._append_issue(
                            issues,
                            "SECURITY",
                            rel,
                            int(getattr(node, "lineno", 0) or 0),
                            f"Olası sabit kimlik bilgisi: {target.id}",
                        )
