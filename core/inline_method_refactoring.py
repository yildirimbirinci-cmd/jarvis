"""Conservative same-file Inline Method refactoring for Python.

Only functions/methods whose body is a single ``return`` expression are
supported.  The service prepares an EditProposal through the refactoring
coordinator and never writes files before explicit approval.
"""
from __future__ import annotations

import ast
import copy
import json
import keyword
from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.core.refactoring_coordinator import (
    RefactoringCoordinator,
    RefactoringKind,
    RefactoringPlan,
)
from artmach_assistant.core.workspace import WorkspaceError


@dataclass(frozen=True, slots=True)
class InlineMethodRequest:
    path: str
    symbol_name: str
    remove_definition: bool = True


class _ParameterSubstituter(ast.NodeTransformer):
    def __init__(self, replacements: dict[str, ast.expr]) -> None:
        self._replacements = replacements

    def visit_Name(self, node: ast.Name) -> ast.AST:
        replacement = self._replacements.get(node.id)
        if replacement is None or not isinstance(node.ctx, ast.Load):
            return node
        return ast.copy_location(copy.deepcopy(replacement), node)


def _safe_text(value: object, *, max_chars: int = 20_000) -> str:
    try:
        text = str(value or "")
    except BaseException:
        return ""
    return text.replace("\x00", "")[:max_chars]


class InlineMethodRefactoring:
    def __init__(self, coordinator: RefactoringCoordinator) -> None:
        self._coordinator = coordinator

    def prepare(self, request: InlineMethodRequest) -> RefactoringPlan:
        path = _safe_text(request.path).strip().replace("\\", "/")
        symbol = _safe_text(request.symbol_name, max_chars=256).strip()
        if path.startswith("/") or ".." in Path(path).parts:
            raise WorkspaceError("Kaynak dosya proje içinde göreli bir yol olmalı.")
        if not isinstance(request.remove_definition, bool):
            raise WorkspaceError("remove_definition yalnızca boolean olmalı.")
        if not path.endswith(".py"):
            raise WorkspaceError("Inline Method şu anda yalnızca Python dosyalarını destekliyor.")
        if not symbol.isidentifier() or keyword.iskeyword(symbol):
            raise WorkspaceError("Inline edilecek sembol adı geçerli bir Python tanımlayıcısı olmalı.")

        workspace = self._coordinator._editor.workspace
        source = workspace.read_text(path, max_chars=2_000_001)
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise WorkspaceError(f"Kaynak dosya ayrıştırılamadı: {exc}") from exc

        target, owner = self._find_target(tree, symbol)
        expression = self._validate_target(target, symbol)
        replacements = self._build_replacements(source, tree, target, owner, expression)
        if not replacements:
            raise WorkspaceError(f"'{symbol}' için güvenle inline edilebilen çağrı bulunamadı.")

        edits = list(replacements)
        if request.remove_definition:
            remaining = self._remaining_references(tree, target, owner, replacements)
            if remaining:
                raise WorkspaceError(
                    f"'{symbol}' tanımına çözümlenemeyen veya çağrı dışı referanslar bulunduğu için tanım kaldırılamıyor."
                )
            edits.append(self._definition_edit(source, target))

        new_source = self._apply_edits(source, edits)
        try:
            compile(new_source, path, "exec")
        except SyntaxError as exc:
            raise WorkspaceError(f"Inline sonucu geçersiz Python üretti: {exc}") from exc

        raw = json.dumps(
            {
                "summary": f"{path} içinde {symbol} çağrıları inline edildi",
                "files": [
                    {
                        "path": path,
                        "content": new_source,
                        "reason": "Basit fonksiyon/metot çağrılarını doğrudan ifade ile değiştir.",
                    }
                ],
            },
            ensure_ascii=False,
        )
        return self._coordinator.prepare(raw, kind=RefactoringKind.INLINE_METHOD)

    @staticmethod
    def _find_target(
        tree: ast.Module, symbol: str
    ) -> tuple[ast.FunctionDef, ast.ClassDef | None]:
        matches: list[tuple[ast.FunctionDef, ast.ClassDef | None]] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == symbol:
                matches.append((node, None))
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name == symbol:
                        matches.append((child, node))
        if not matches:
            raise WorkspaceError(f"'{symbol}' adlı fonksiyon veya metot bulunamadı.")
        if len(matches) != 1:
            raise WorkspaceError(f"'{symbol}' adı birden fazla tanıma çözümleniyor.")
        return matches[0]

    @staticmethod
    def _validate_target(target: ast.FunctionDef, symbol: str) -> ast.expr:
        if target.decorator_list:
            raise WorkspaceError("Decorator içeren fonksiyonlar güvenle inline edilemiyor.")
        if len(target.body) != 1 or not isinstance(target.body[0], ast.Return):
            raise WorkspaceError("Yalnızca tek bir return ifadesinden oluşan fonksiyonlar inline edilebilir.")
        if target.body[0].value is None:
            raise WorkspaceError("Değer döndürmeyen fonksiyon inline edilemez.")
        if target.args.posonlyargs or target.args.kwonlyargs or target.args.vararg or target.args.kwarg:
            raise WorkspaceError("Karmaşık parametre imzası güvenli inline kapsamı dışında.")
        for node in ast.walk(target.body[0].value):
            if isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)):
                raise WorkspaceError(f"{type(node).__name__} içeren ifade inline edilemez.")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == symbol:
                    raise WorkspaceError("Özyinelemeli fonksiyon inline edilemez.")
                if isinstance(node.func, ast.Attribute) and node.func.attr == symbol:
                    raise WorkspaceError("Özyinelemeli metot inline edilemez.")
        return target.body[0].value

    def _build_replacements(
        self,
        source: str,
        tree: ast.Module,
        target: ast.FunctionDef,
        owner: ast.ClassDef | None,
        expression: ast.expr,
    ) -> list[tuple[int, int, str, ast.AST]]:
        line_offsets = self._line_offsets(source)
        edits: list[tuple[int, int, str, ast.AST]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or self._inside(node, target):
                continue
            receiver: ast.expr | None = None
            if owner is None:
                if not isinstance(node.func, ast.Name) or node.func.id != target.name:
                    continue
            else:
                if not isinstance(node.func, ast.Attribute) or node.func.attr != target.name:
                    continue
                receiver = node.func.value

            mapping = self._bind_arguments(target, node, receiver)
            transformed = _ParameterSubstituter(mapping).visit(copy.deepcopy(expression))
            ast.fix_missing_locations(transformed)
            replacement = ast.unparse(transformed)
            start, end = self._node_offsets(line_offsets, node)
            edits.append((start, end, replacement, node))
        return edits

    @staticmethod
    def _bind_arguments(
        target: ast.FunctionDef, call: ast.Call, receiver: ast.expr | None
    ) -> dict[str, ast.expr]:
        if any(keyword.arg is None for keyword in call.keywords):
            raise WorkspaceError("**kwargs kullanılan çağrı güvenle inline edilemiyor.")
        params = [arg.arg for arg in target.args.args]
        defaults = list(target.args.defaults)
        required = len(params) - len(defaults)
        mapping: dict[str, ast.expr] = {}
        position = 0
        if receiver is not None:
            if not params:
                raise WorkspaceError("Metot alıcı parametresi bulunamadı.")
            mapping[params[0]] = receiver
            position = 1
        if len(call.args) > len(params) - position:
            raise WorkspaceError("Fazla positional argüman içeren çağrı inline edilemiyor.")
        for value in call.args:
            mapping[params[position]] = value
            position += 1
        for item in call.keywords:
            assert item.arg is not None
            if item.arg not in params or item.arg in mapping:
                raise WorkspaceError("Geçersiz veya yinelenen keyword argümanı.")
            mapping[item.arg] = item.value
        default_map = dict(zip(params[required:], defaults))
        for name in params:
            if name not in mapping:
                default = default_map.get(name)
                if default is None:
                    raise WorkspaceError(f"'{name}' argümanı eksik.")
                mapping[name] = default
        return mapping

    @staticmethod
    def _remaining_references(
        tree: ast.Module,
        target: ast.FunctionDef,
        owner: ast.ClassDef | None,
        replacements: list[tuple[int, int, str, ast.AST]],
    ) -> list[ast.AST]:
        replaced_ids = {id(child) for edit in replacements for child in ast.walk(edit[3])}
        remaining: list[ast.AST] = []
        for node in ast.walk(tree):
            if InlineMethodRefactoring._inside(node, target):
                continue
            if id(node) in replaced_ids:
                continue
            if owner is None and isinstance(node, ast.Name) and node.id == target.name:
                if not isinstance(node.ctx, ast.Store):
                    remaining.append(node)
            elif owner is not None and isinstance(node, ast.Attribute) and node.attr == target.name:
                remaining.append(node)
        return remaining

    @staticmethod
    def _inside(node: ast.AST, target: ast.FunctionDef) -> bool:
        return node is target or any(child is node for child in ast.walk(target))

    @staticmethod
    def _definition_edit(source: str, target: ast.FunctionDef) -> tuple[int, int, str, ast.AST]:
        offsets = InlineMethodRefactoring._line_offsets(source)
        start_line = min([target.lineno] + [item.lineno for item in target.decorator_list])
        start = offsets[start_line - 1]
        end_line = getattr(target, "end_lineno", target.lineno)
        end = offsets[end_line] if end_line < len(offsets) else len(source)
        # Consume one following blank line, but keep surrounding code untouched.
        if end < len(source):
            next_end = source.find("\n", end)
            segment = source[end: next_end + 1 if next_end >= 0 else len(source)]
            if not segment.strip():
                end = next_end + 1 if next_end >= 0 else len(source)
        return start, end, "", target

    @staticmethod
    def _line_offsets(source: str) -> list[int]:
        offsets = [0]
        for index, char in enumerate(source):
            if char == "\n":
                offsets.append(index + 1)
        return offsets

    @staticmethod
    def _node_offsets(offsets: list[int], node: ast.AST) -> tuple[int, int]:
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        return start, end

    @staticmethod
    def _apply_edits(source: str, edits: list[tuple[int, int, str, ast.AST]]) -> str:
        result = source
        occupied: list[tuple[int, int]] = []
        for start, end, replacement, _ in sorted(edits, key=lambda item: item[0], reverse=True):
            if any(not (end <= left or start >= right) for left, right in occupied):
                raise WorkspaceError("Çakışan inline düzenlemeleri güvenle uygulanamıyor.")
            result = result[:start] + replacement + result[end:]
            occupied.append((start, end))
        return result
