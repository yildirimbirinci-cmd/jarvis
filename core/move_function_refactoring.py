"""Safe, staged Python Move Function refactoring.

This conservative implementation moves one top-level function or async function
between Python modules, copies imports required by the function, updates explicit
``from source import function`` statements, and keeps source-local callers valid.
No workspace file is written before :class:`RefactoringCoordinator` approval.
"""
from __future__ import annotations

import ast
import builtins
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
class MoveFunctionRequest:
    source_path: str
    function_name: str
    destination_path: str


def _safe_text(value: object, *, max_chars: int = 20_000) -> str:
    try:
        text = str(value or "")
    except BaseException:
        return ""
    return text.replace("\x00", "")[:max_chars]


class MoveFunctionRefactoring:
    def __init__(self, coordinator: RefactoringCoordinator) -> None:
        self._coordinator = coordinator

    def prepare(self, request: MoveFunctionRequest) -> RefactoringPlan:
        source_path = self._python_path(request.source_path, "Kaynak")
        destination_path = self._python_path(request.destination_path, "Hedef")
        function_name = _safe_text(request.function_name, max_chars=256).strip()
        if not function_name.isidentifier():
            raise WorkspaceError("Taşınacak fonksiyon adı geçerli bir Python tanımlayıcısı olmalı.")
        if source_path == destination_path:
            raise WorkspaceError("Kaynak ve hedef dosya aynı olamaz.")

        workspace = self._coordinator._editor.workspace
        root = Path(workspace.require_root()).resolve(strict=False)
        source = workspace.read_text(source_path, max_chars=2_000_001)
        destination = self._read_optional(workspace, destination_path)
        source_tree = self._parse(source, source_path)
        destination_tree = self._parse(destination, destination_path)

        function_node = self._find_top_level_function(source_tree, function_name)
        if self._find_top_level_function(destination_tree, function_name, required=False) is not None:
            raise WorkspaceError(f"Hedef dosyada zaten {function_name} adlı bir fonksiyon var.")
        if self._has_relative_import(source_tree):
            raise WorkspaceError(
                "Kaynak dosyada göreli import bulundu; güvenli taşıma için önce mutlak import kullan."
            )

        required_imports = self._required_imports(source, source_tree, function_node)
        self._reject_source_local_dependencies(source_tree, function_node, required_imports)
        function_text = self._node_text(source, function_node)
        updated_source = self._remove_node(source, function_node)

        source_module = self._module_name(source_path)
        destination_module = self._module_name(destination_path)
        if self._name_is_loaded(self._parse(updated_source, source_path), function_name):
            updated_source = self._insert_import(
                updated_source, f"from {destination_module} import {function_name}"
            )
        updated_destination = self._append_function(destination, required_imports, function_text)

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
                function_name=function_name,
                path=relative,
            )
            if rewritten != text:
                changes[relative] = rewritten

        if len(changes) > 8:
            raise WorkspaceError("Move Function işlemi tek transaction sınırı olan 8 dosyayı aşıyor.")
        files = [
            {
                "path": path,
                "content": changes[path],
                "reason": (
                    f"{function_name} fonksiyonunu {source_path} dosyasından "
                    f"{destination_path} dosyasına taşı."
                ),
            }
            for path in sorted(changes, key=str.casefold)
        ]
        raw = json.dumps(
            {
                "summary": f"{function_name} fonksiyonu {destination_path} dosyasına taşındı",
                "files": files,
            },
            ensure_ascii=False,
        )
        return self._coordinator.prepare(
            raw,
            kind=RefactoringKind.MOVE_FUNCTION,
            symbol=function_name,
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
    def _find_top_level_function(
        tree: ast.Module,
        function_name: str,
        *,
        required: bool = True,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        matches = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ]
        if len(matches) > 1:
            raise WorkspaceError(f"Birden fazla üst seviye {function_name} fonksiyonu bulundu.")
        if not matches:
            if required:
                raise WorkspaceError(f"Üst seviye {function_name} fonksiyonu bulunamadı.")
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
            raise WorkspaceError("Fonksiyon kaynak metni okunamadı.")
        return text

    @staticmethod
    def _remove_node(source: str, node: ast.AST) -> str:
        lines = source.splitlines(keepends=True)
        start = int(getattr(node, "lineno", 1)) - 1
        end = int(getattr(node, "end_lineno", start + 1))
        while end < len(lines) and not lines[end].strip():
            end += 1
        result = "".join(lines[:start] + lines[end:])
        return result.lstrip("\n") if start == 0 else result

    @staticmethod
    def _import_bindings(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Import):
            return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
        if isinstance(node, ast.ImportFrom):
            return {alias.asname or alias.name for alias in node.names if alias.name != "*"}
        return set()

    @classmethod
    def _required_imports(
        cls,
        source: str,
        tree: ast.Module,
        function_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[str, ...]:
        loaded = cls._loaded_names(function_node)
        rows: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)) and cls._import_bindings(node) & loaded:
                rows.append(cls._node_text(source, node))
        return tuple(dict.fromkeys(rows))

    @classmethod
    def _reject_source_local_dependencies(
        cls,
        tree: ast.Module,
        function_node: ast.FunctionDef | ast.AsyncFunctionDef,
        required_imports: tuple[str, ...],
    ) -> None:
        parameters = {
            arg.arg
            for arg in (
                list(function_node.args.posonlyargs)
                + list(function_node.args.args)
                + list(function_node.args.kwonlyargs)
            )
        }
        if function_node.args.vararg:
            parameters.add(function_node.args.vararg.arg)
        if function_node.args.kwarg:
            parameters.add(function_node.args.kwarg.arg)
        local_stores = {
            node.id
            for node in ast.walk(function_node)
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
        }
        imported: set[str] = set()
        for row in required_imports:
            imported.update(cls._import_bindings(ast.parse(row).body[0]))
        allowed = parameters | local_stores | imported | set(dir(builtins))
        loaded = cls._loaded_names(function_node)
        module_bindings = cls._module_bindings(tree) - {function_node.name}
        unsafe = sorted((loaded & module_bindings) - allowed, key=str.casefold)
        if unsafe:
            raise WorkspaceError(
                "Fonksiyon kaynak modüldeki yerel tanımlara bağlı: " + ", ".join(unsafe)
            )

    @staticmethod
    def _loaded_names(node: ast.AST) -> set[str]:
        return {
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }

    @classmethod
    def _module_bindings(cls, tree: ast.Module) -> set[str]:
        names: set[str] = set()
        for node in tree.body:
            names.update(cls._import_bindings(node))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    names.update(
                        child.id for child in ast.walk(target) if isinstance(child, ast.Name)
                    )
        return names

    @staticmethod
    def _name_is_loaded(tree: ast.Module, name: str) -> bool:
        return any(
            isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == name
            for node in ast.walk(tree)
        )

    @staticmethod
    def _insert_import(source: str, import_row: str) -> str:
        tree = ast.parse(source or "")
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and not node.level:
                if ast.get_source_segment(source, node) == import_row:
                    return source
        lines = source.splitlines(keepends=True)
        insert_line = 0
        if tree.body and isinstance(tree.body[0], ast.Expr):
            value = tree.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                insert_line = int(tree.body[0].end_lineno or 0)
        while insert_line < len(lines):
            stripped = lines[insert_line].strip()
            if stripped.startswith("from __future__ import"):
                insert_line += 1
                continue
            break
        lines.insert(insert_line, import_row + "\n")
        if insert_line + 1 < len(lines) and lines[insert_line + 1].strip():
            lines.insert(insert_line + 1, "\n")
        result = "".join(lines)
        compile(result, "<move-function-source>", "exec")
        return result

    @staticmethod
    def _append_function(destination: str, imports: tuple[str, ...], function_text: str) -> str:
        existing = destination.rstrip()
        existing_imports: set[str] = set()
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
        parts.append(function_text)
        result = "\n\n".join(part for part in parts if part).rstrip() + "\n"
        try:
            compile(result, "<move-function-destination>", "exec")
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
        function_name: str,
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
            moved = [alias for alias in node.names if alias.name == function_name]
            if not moved:
                continue
            remaining = [alias for alias in node.names if alias.name != function_name]
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
