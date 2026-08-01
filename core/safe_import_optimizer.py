"""Conservative, staged Python import optimization."""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.core.refactoring_coordinator import (
    RefactoringCoordinator,
    RefactoringKind,
    RefactoringPlan,
)
from artmach_assistant.core.workspace import WorkspaceError

_MAX_PATH_CHARS = 4096
_MAX_SOURCE_CHARS = 2_000_000
_MAX_REMOVED_BINDINGS = 10_000


def _safe_text(value: object, *, field: str, max_chars: int) -> str:
    try:
        text = str(value)
    except BaseException as exc:
        raise WorkspaceError(f"{field} metne dönüştürülemedi.") from exc
    if "\x00" in text:
        raise WorkspaceError(f"{field} NUL karakteri içeremez.")
    if len(text) > max_chars:
        raise WorkspaceError(f"{field} en fazla {max_chars} karakter olabilir.")
    return text


@dataclass(frozen=True, slots=True)
class ImportOptimizationResult:
    path: str
    content: str
    removed_bindings: tuple[str, ...]
    removed_duplicates: int
    preserved_risky_imports: int

    @property
    def changed(self) -> bool:
        return bool(self.removed_bindings or self.removed_duplicates)


class SafeImportOptimizer:
    """Prepare conservative import cleanup proposals for Python files."""

    def __init__(self, coordinator: RefactoringCoordinator) -> None:
        self._coordinator = coordinator

    def analyze(self, path: str) -> ImportOptimizationResult:
        relative_path = self._python_path(path)
        workspace = self._coordinator._editor.workspace
        source = workspace.read_text(relative_path, max_chars=_MAX_SOURCE_CHARS + 1)
        return self.analyze_content(relative_path, source)

    def analyze_content(self, path: str, source: str) -> ImportOptimizationResult:
        relative_path = self._python_path(path)
        if not isinstance(source, str):
            raise WorkspaceError("Import analizi için metin içerik gerekli.")
        if len(source) > _MAX_SOURCE_CHARS:
            raise WorkspaceError("Import analizi için kaynak dosya çok büyük.")
        if "\x00" in source:
            raise WorkspaceError("Import analizi için kaynak NUL karakteri içeremez.")
        tree = self._parse(source, relative_path)
        used_names = self._loaded_names(tree) | self._exported_names(tree)
        lines = source.splitlines(keepends=True)
        replacements: list[tuple[int, int, str]] = []
        removed: list[str] = []
        duplicate_count = 0
        risky_count = 0
        seen_imports: set[str] = set()

        for node in tree.body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            start = int(node.lineno) - 1
            end = int(getattr(node, "end_lineno", node.lineno))
            original = "".join(lines[start:end])
            if self._is_risky(node, original):
                risky_count += 1
                continue
            fingerprint = ast.dump(node, annotate_fields=True, include_attributes=False)
            if fingerprint in seen_imports and not self._has_protective_comment(original):
                replacements.append((start, end, ""))
                duplicate_count += 1
                continue
            seen_imports.add(fingerprint)
            kept_aliases: list[ast.alias] = []
            removed_here: list[str] = []
            for alias in node.names:
                binding = self._binding_name(node, alias)
                if binding and binding not in used_names:
                    removed_here.append(binding)
                else:
                    kept_aliases.append(alias)
            if not removed_here:
                continue
            if len(removed) + len(removed_here) > _MAX_REMOVED_BINDINGS:
                raise WorkspaceError("Import analizi kaldırılacak bağ sınırını aştı.")
            removed.extend(removed_here)
            if not kept_aliases:
                replacements.append((start, end, ""))
                continue
            replacements.append((start, end, self._rewrite_single_line(node, kept_aliases, original)))

        content = self._apply_replacements(lines, replacements)
        if content:
            self._parse(content, relative_path)
        return ImportOptimizationResult(
            path=relative_path,
            content=content,
            removed_bindings=tuple(dict.fromkeys(removed)),
            removed_duplicates=duplicate_count,
            preserved_risky_imports=risky_count,
        )

    def prepare(self, path: str) -> RefactoringPlan:
        result = self.analyze(path)
        if not result.changed:
            raise WorkspaceError("Dosyada güvenle optimize edilebilecek import bulunamadı.")
        details: list[str] = []
        if result.removed_bindings:
            details.append("kullanılmayan bağlar: " + ", ".join(result.removed_bindings))
        if result.removed_duplicates:
            details.append(f"yinelenen import: {result.removed_duplicates}")
        reason = "Güvenli import optimizasyonu; " + "; ".join(details)
        raw = json.dumps({"summary": f"{result.path} importları güvenli şekilde optimize edildi", "files": [{"path": result.path, "content": result.content, "reason": reason}]}, ensure_ascii=False, sort_keys=True)
        return self._coordinator.prepare(raw, kind=RefactoringKind.OPTIMIZE_IMPORTS)

    @staticmethod
    def _python_path(value: object) -> str:
        path = _safe_text(value or "", field="Dosya yolu", max_chars=_MAX_PATH_CHARS).strip().replace("\\", "/")
        if not path or not path.casefold().endswith(".py"):
            raise WorkspaceError("Import optimizasyonu için proje içindeki bir Python dosyası gerekli.")
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorkspaceError("Dosya yolu proje içinde göreli olmalı.")
        normalized = candidate.as_posix()
        if normalized in {".", ""}:
            raise WorkspaceError("Dosya yolu boş olamaz.")
        return normalized

    @staticmethod
    def _parse(source: str, path: str) -> ast.Module:
        try:
            return ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise WorkspaceError(f"Geçersiz Python dosyası: {path}:{exc.lineno}: {exc.msg}") from exc

    @staticmethod
    def _loaded_names(tree: ast.Module) -> set[str]:
        return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}

    @staticmethod
    def _exported_names(tree: ast.Module) -> set[str]:
        exported: set[str] = set()
        for node in tree.body:
            value: ast.AST | None = None
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
                value = node.value
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                for item in value.elts:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        exported.add(item.value)
        return exported

    @classmethod
    def _is_risky(cls, node: ast.Import | ast.ImportFrom, original: str) -> bool:
        if int(getattr(node, "end_lineno", node.lineno)) != int(node.lineno):
            return True
        if ";" in original or cls._has_protective_comment(original):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__" or node.level:
                return True
            if any(alias.name == "*" for alias in node.names):
                return True
        return False

    @staticmethod
    def _has_protective_comment(text: str) -> bool:
        lowered = text.casefold()
        return "# noqa" in lowered or "# type: ignore" in lowered

    @staticmethod
    def _binding_name(node: ast.Import | ast.ImportFrom, alias: ast.alias) -> str:
        if alias.name == "*":
            return ""
        if alias.asname:
            return alias.asname
        if isinstance(node, ast.Import):
            return alias.name.split(".", 1)[0]
        return alias.name

    @staticmethod
    def _rewrite_single_line(node: ast.Import | ast.ImportFrom, aliases: list[ast.alias], original: str) -> str:
        indentation = original[: len(original) - len(original.lstrip(" \t"))]
        newline = "\r\n" if original.endswith("\r\n") else "\n" if original.endswith("\n") else ""
        comment = ""
        body = original.rstrip("\r\n")
        hash_index = body.find("#")
        if hash_index >= 0:
            comment = "  " + body[hash_index:].strip()
        rewritten_node: ast.stmt
        if isinstance(node, ast.Import):
            rewritten_node = ast.Import(names=aliases)
        else:
            rewritten_node = ast.ImportFrom(module=node.module, names=aliases, level=node.level)
        ast.fix_missing_locations(rewritten_node)
        return indentation + ast.unparse(rewritten_node) + comment + newline

    @staticmethod
    def _apply_replacements(lines: list[str], replacements: list[tuple[int, int, str]]) -> str:
        updated = list(lines)
        for start, end, replacement in sorted(replacements, reverse=True):
            if start < 0 or end < start or end > len(updated):
                raise WorkspaceError("Import değiştirme aralığı geçersiz.")
            updated[start:end] = [replacement] if replacement else []
        return "".join(updated)
