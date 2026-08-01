"""Safe, staged Python Move Class refactoring.

The implementation deliberately supports a conservative subset: one top-level
class is moved between Python modules, required source imports are copied, and
explicit ``from source import Class`` imports inside the workspace are updated.
No file is written before :class:`RefactoringCoordinator` approval.
"""
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


@dataclass(frozen=True, slots=True)
class MoveClassRequest:
    source_path: str
    class_name: str
    destination_path: str


def _safe_text(value: object, *, max_chars: int = 20_000) -> str:
    try:
        text = str(value or "")
    except BaseException:
        return ""
    return text.replace("\x00", "")[:max_chars]


class MoveClassRefactoring:
    def __init__(self, coordinator: RefactoringCoordinator) -> None:
        self._coordinator = coordinator

    def prepare(self, request: MoveClassRequest) -> RefactoringPlan:
        source_path = self._python_path(request.source_path, "Kaynak")
        destination_path = self._python_path(request.destination_path, "Hedef")
        class_name = _safe_text(request.class_name, max_chars=256).strip()
        if not class_name.isidentifier():
            raise WorkspaceError("Taşınacak sınıf adı geçerli bir Python tanımlayıcısı olmalı.")
        if source_path == destination_path:
            raise WorkspaceError("Kaynak ve hedef dosya aynı olamaz.")

        workspace = self._coordinator._editor.workspace
        root = Path(workspace.require_root()).resolve(strict=False)
        source = workspace.read_text(source_path, max_chars=2_000_001)
        destination = self._read_optional(workspace, destination_path)
        source_tree = self._parse(source, source_path)
        destination_tree = self._parse(destination, destination_path)

        class_node = self._find_top_level_class(source_tree, class_name)
        if self._find_top_level_class(destination_tree, class_name, required=False) is not None:
            raise WorkspaceError(f"Hedef dosyada zaten {class_name} adlı bir sınıf var.")
        if self._has_relative_import(source_tree):
            raise WorkspaceError(
                "Kaynak dosyada göreli import bulundu; güvenli taşıma için önce mutlak import kullan."
            )

        class_text = self._node_text(source, class_node)
        required_imports = self._required_imports(source, source_tree, class_node)
        updated_source = self._remove_node(source, class_node)
        updated_destination = self._append_class(destination, required_imports, class_text)

        source_module = self._module_name(source_path)
        destination_module = self._module_name(destination_path)
        changes: dict[str, str] = {
            source_path: updated_source,
            destination_path: updated_destination,
        }
        for path in self._python_files(root):
            relative = path.relative_to(root).as_posix()
            if relative in changes:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            rewritten = self._rewrite_explicit_import(
                text,
                source_module=source_module,
                destination_module=destination_module,
                class_name=class_name,
                path=relative,
            )
            if rewritten != text:
                changes[relative] = rewritten

        if len(changes) > 8:
            raise WorkspaceError("Move Class işlemi tek transaction sınırı olan 8 dosyayı aşıyor.")
        files = [
            {
                "path": path,
                "content": changes[path],
                "reason": f"{class_name} sınıfını {source_path} dosyasından {destination_path} dosyasına taşı.",
            }
            for path in sorted(changes, key=str.casefold)
        ]
        raw = json.dumps(
            {"summary": f"{class_name} sınıfı {destination_path} dosyasına taşındı", "files": files},
            ensure_ascii=False,
        )
        return self._coordinator.prepare(
            raw,
            kind=RefactoringKind.MOVE_CLASS,
            symbol=class_name,
        )

    @staticmethod
    def _python_path(value: str, label: str) -> str:
        path = _safe_text(value).strip().replace("\\", "/")
        if not path or not path.endswith(".py"):
            raise WorkspaceError(f"{label} dosya bir Python (.py) dosyası olmalı.")
        if path.startswith("/") or ".." in Path(path).parts:
            raise WorkspaceError(f"{label} dosya proje içinde göreli bir yol olmalı.")
        return Path(path).as_posix()

    @staticmethod
    def _read_optional(workspace: object, path: str) -> str:
        try:
            return workspace.read_text(path, max_chars=2_000_001)
        except (FileNotFoundError, OSError):
            return ""

    @staticmethod
    def _parse(source: str, path: str) -> ast.Module:
        try:
            return ast.parse(source or "", filename=path)
        except SyntaxError as exc:
            raise WorkspaceError(f"Geçersiz Python dosyası: {path}:{exc.lineno}: {exc.msg}") from exc

    @staticmethod
    def _find_top_level_class(
        tree: ast.Module,
        class_name: str,
        *,
        required: bool = True,
    ) -> ast.ClassDef | None:
        matches = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
        if len(matches) > 1:
            raise WorkspaceError(f"Birden fazla üst seviye {class_name} sınıfı bulundu.")
        if not matches:
            if required:
                raise WorkspaceError(f"Üst seviye {class_name} sınıfı bulunamadı.")
            return None
        return matches[0]

    @staticmethod
    def _has_relative_import(tree: ast.Module) -> bool:
        return any(isinstance(node, ast.ImportFrom) and node.level for node in tree.body)

    @staticmethod
    def _node_text(source: str, node: ast.AST) -> str:
        lines = source.splitlines(keepends=True)
        start = int(getattr(node, "lineno", 1)) - 1
        end = int(getattr(node, "end_lineno", start + 1))
        text = "".join(lines[start:end]).rstrip()
        if not text:
            raise WorkspaceError("Sınıf kaynak metni okunamadı.")
        return text

    @staticmethod
    def _remove_node(source: str, node: ast.AST) -> str:
        lines = source.splitlines(keepends=True)
        start = int(getattr(node, "lineno", 1)) - 1
        end = int(getattr(node, "end_lineno", start + 1))
        while end < len(lines) and not lines[end].strip():
            end += 1
        result = "".join(lines[:start] + lines[end:])
        return result.lstrip("\n") if not result[:start] else result

    @staticmethod
    def _required_imports(source: str, tree: ast.Module, class_node: ast.ClassDef) -> tuple[str, ...]:
        loaded = {
            node.id
            for node in ast.walk(class_node)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        rows: list[str] = []
        for node in tree.body:
            names: set[str] = set()
            if isinstance(node, ast.Import):
                names = {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {alias.asname or alias.name for alias in node.names if alias.name != "*"}
            if names & loaded:
                rows.append(MoveClassRefactoring._node_text(source, node))
        return tuple(dict.fromkeys(rows))

    @staticmethod
    def _append_class(destination: str, imports: tuple[str, ...], class_text: str) -> str:
        existing = destination.rstrip()
        existing_imports = set()
        if destination.strip():
            tree = ast.parse(destination)
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    existing_imports.add(ast.get_source_segment(destination, node) or "")
        missing = [row for row in imports if row not in existing_imports]
        parts: list[str] = []
        if existing:
            parts.append(existing)
        if missing:
            parts.append("\n".join(missing))
        parts.append(class_text)
        result = "\n\n".join(part for part in parts if part).rstrip() + "\n"
        try:
            compile(result, "<move-class-destination>", "exec")
        except SyntaxError as exc:
            raise WorkspaceError(f"Taşıma sonucu hedef dosya geçersiz Python üretti: {exc}") from exc
        return result

    @staticmethod
    def _module_name(path: str) -> str:
        parts = list(Path(path).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    @staticmethod
    def _python_files(root: Path) -> tuple[Path, ...]:
        files: list[Path] = []
        for path in root.rglob("*.py"):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                if path.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            files.append(path)
            if len(files) > 20_000:
                raise WorkspaceError("Projedeki Python dosyası sayısı güvenli tarama sınırını aşıyor.")
        return tuple(sorted(files, key=lambda item: item.as_posix().casefold()))

    @staticmethod
    def _rewrite_explicit_import(
        source: str,
        *,
        source_module: str,
        destination_module: str,
        class_name: str,
        path: str,
    ) -> str:
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise WorkspaceError(f"Import kullanan dosya geçersiz Python: {path}:{exc.lineno}") from exc
        edits: list[tuple[int, int, str]] = []
        lines = source.splitlines(keepends=True)
        offsets: list[int] = []
        total = 0
        for line in lines:
            offsets.append(total)
            total += len(line)
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level or node.module != source_module:
                continue
            moved = [alias for alias in node.names if alias.name == class_name]
            if not moved:
                continue
            remaining = [alias for alias in node.names if alias.name != class_name]
            moved_text = ", ".join(
                alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"
                for alias in moved
            )
            replacement_rows = [f"from {destination_module} import {moved_text}"]
            if remaining:
                remaining_text = ", ".join(
                    alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"
                    for alias in remaining
                )
                replacement_rows.insert(0, f"from {source_module} import {remaining_text}")
            start = offsets[node.lineno - 1] + node.col_offset
            end_line = int(node.end_lineno or node.lineno)
            end = offsets[end_line - 1] + int(node.end_col_offset or 0)
            edits.append((start, end, "\n".join(replacement_rows)))
        result = source
        for start, end, replacement in sorted(edits, reverse=True):
            result = result[:start] + replacement + result[end:]
        if result != source:
            compile(result, path, "exec")
        return result
