"""Conservative Python Extract Method refactoring.

The service only prepares a validated refactoring plan. It never writes source
files directly; application remains behind RefactoringCoordinator approval.
"""
from __future__ import annotations

import ast
import builtins
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
class ExtractMethodRequest:
    path: str
    start_line: int
    end_line: int
    new_name: str
    preserve_loop_control: bool = False


@dataclass(frozen=True, slots=True)
class ExtractMethodAnalysis:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    is_method: bool
    is_async: bool


class _NameFacts(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loads: dict[str, tuple[int, int]] = {}
        self.stores: dict[str, tuple[int, int]] = {}

    def visit_Name(self, node: ast.Name) -> None:
        pos = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
        target = self.loads if isinstance(node.ctx, ast.Load) else self.stores
        if isinstance(node.ctx, (ast.Load, ast.Store, ast.Del)):
            target.setdefault(node.id, pos)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Nested scopes do not belong to the selected block's data flow.
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _safe_text(value: object, *, max_chars: int = 20_000) -> str:
    try:
        text = str(value or "")
    except BaseException:
        return ""
    return text.replace("\x00", "")[:max_chars]


class ExtractMethodRefactoring:
    def __init__(self, coordinator: RefactoringCoordinator) -> None:
        self._coordinator = coordinator

    def prepare(self, request: ExtractMethodRequest) -> RefactoringPlan:
        path = _safe_text(request.path).strip().replace("\\", "/")
        name = _safe_text(request.new_name, max_chars=256).strip()
        if path.startswith("/") or ".." in Path(path).parts:
            raise WorkspaceError("Kaynak dosya proje içinde göreli bir yol olmalı.")
        if not path.endswith(".py"):
            raise WorkspaceError("Extract Method şu anda yalnızca Python dosyalarını destekliyor.")
        if not name.isidentifier() or keyword.iskeyword(name):
            raise WorkspaceError("Yeni metot adı geçerli bir Python tanımlayıcısı olmalı.")
        if isinstance(request.start_line, bool) or isinstance(request.end_line, bool):
            raise WorkspaceError("Satır aralığı tam sayı olmalı.")
        if not isinstance(request.start_line, int) or not isinstance(request.end_line, int):
            raise WorkspaceError("Satır aralığı tam sayı olmalı.")
        if request.start_line < 1 or request.end_line < request.start_line:
            raise WorkspaceError("Geçersiz satır aralığı.")

        workspace = self._coordinator._editor.workspace
        source = workspace.read_text(path, max_chars=2_000_001)
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise WorkspaceError(f"Kaynak dosya ayrıştırılamadı: {exc}") from exc

        function, owner_class = self._find_enclosing_function(
            tree, request.start_line, request.end_line
        )
        selected = self._select_statements(function, request.start_line, request.end_line)
        self._validate_selected_nodes(
            selected,
            preserve_loop_control=bool(request.preserve_loop_control),
        )
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
            for node in tree.body
        ):
            raise WorkspaceError(f"Aynı kapsamda '{name}' adlı bir tanım zaten var.")
        if owner_class and any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
            for node in owner_class.body
        ):
            raise WorkspaceError(f"Sınıfta '{name}' adlı bir metot zaten var.")

        analysis = self._analyze(function, selected, owner_class is not None)
        new_source = self._rewrite(
            source,
            function=function,
            owner_class=owner_class,
            selected=selected,
            name=name,
            analysis=analysis,
            preserve_loop_control=bool(request.preserve_loop_control),
        )
        raw = json.dumps(
            {
                "summary": f"{path} içinde {name} metodu çıkarıldı",
                "files": [
                    {
                        "path": path,
                        "content": new_source,
                        "reason": "Seçili kod bloğunu ayrı ve yeniden kullanılabilir bir metoda çıkar.",
                    }
                ],
            },
            ensure_ascii=False,
        )
        return self._coordinator.prepare(raw, kind=RefactoringKind.EXTRACT_METHOD)

    @staticmethod
    def _find_enclosing_function(
        tree: ast.AST, start: int, end: int
    ) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.ClassDef | None]:
        candidates: list[tuple[int, ast.FunctionDef | ast.AsyncFunctionDef, ast.ClassDef | None]] = []
        for parent in ast.walk(tree):
            owner = parent if isinstance(parent, ast.ClassDef) else None
            body = getattr(parent, "body", ())
            if not isinstance(body, (list, tuple)):
                continue
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.lineno <= start and getattr(node, "end_lineno", node.lineno) >= end:
                        span = getattr(node, "end_lineno", node.lineno) - node.lineno
                        candidates.append((span, node, owner))
        if not candidates:
            raise WorkspaceError("Seçili satırlar bir fonksiyon veya metot gövdesinde değil.")
        _, function, owner = min(candidates, key=lambda row: row[0])
        return function, owner

    @staticmethod
    def _select_statements(function: ast.AST, start: int, end: int) -> list[ast.stmt]:
        bodies: list[list[ast.stmt]] = []

        def collect(node: ast.AST) -> None:
            if node is not function and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
            ):
                return
            for _field, value in ast.iter_fields(node):
                if isinstance(value, list) and (
                    not value or all(isinstance(item, ast.stmt) for item in value)
                ):
                    statement_body = [item for item in value if isinstance(item, ast.stmt)]
                    if statement_body:
                        bodies.append(statement_body)
                        for item in statement_body:
                            collect(item)
                elif isinstance(value, ast.AST):
                    collect(value)

        collect(function)
        matches: list[list[ast.stmt]] = []
        for body in bodies:
            selected = [
                stmt for stmt in body
                if stmt.lineno >= start and getattr(stmt, "end_lineno", stmt.lineno) <= end
            ]
            if (
                selected
                and selected[0].lineno == start
                and getattr(selected[-1], "end_lineno", 0) == end
            ):
                first = body.index(selected[0])
                if body[first:first + len(selected)] == selected:
                    matches.append(selected)
        if len(matches) != 1:
            raise WorkspaceError(
                "Satır aralığı aynı blokta tam ve ardışık ifadeler seçmeli."
            )
        return matches[0]

    @staticmethod
    def _validate_selected_nodes(
        selected: list[ast.stmt],
        *,
        preserve_loop_control: bool = False,
    ) -> None:
        forbidden = (ast.Return, ast.Yield, ast.YieldFrom, ast.Global, ast.Nonlocal)
        for stmt in selected:
            for node in ast.walk(stmt):
                if isinstance(node, forbidden):
                    raise WorkspaceError(
                        f"Seçim {type(node).__name__} içerdiği için güvenle çıkarılamıyor."
                    )
                if isinstance(node, (ast.Break, ast.Continue)) and not preserve_loop_control:
                    raise WorkspaceError(
                        f"Seçim {type(node).__name__} içerdiği için güvenle çıkarılamıyor."
                    )
        if preserve_loop_control:
            ExtractMethodRefactoring._validate_external_loop_control(selected)

    @staticmethod
    def _validate_external_loop_control(selected: list[ast.stmt]) -> None:
        """Allow only break/continue statements owned by the caller's loop.

        A break or continue nested in a loop contained by the selection already
        belongs to that nested loop and must not be converted into a helper
        outcome.  Refuse that ambiguous shape instead of changing its meaning.
        """

        def walk(node: ast.AST, nested_loop: int = 0) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                return
            if isinstance(node, (ast.Break, ast.Continue)) and nested_loop:
                raise WorkspaceError(
                    "Seçim iç içe döngü kontrolü içerdiği için güvenle çıkarılamıyor."
                )
            child_depth = nested_loop + int(isinstance(node, (ast.For, ast.AsyncFor, ast.While)))
            for child in ast.iter_child_nodes(node):
                walk(child, child_depth)

        for statement in selected:
            walk(statement)

    @staticmethod
    def _facts(nodes: list[ast.AST]) -> _NameFacts:
        facts = _NameFacts()
        for node in nodes:
            ast.NodeVisitor.generic_visit(facts, node)
        return facts

    def _analyze(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        selected: list[ast.stmt],
        is_method: bool,
    ) -> ExtractMethodAnalysis:
        selected_facts = self._facts(selected)
        selected_end = getattr(selected[-1], "end_lineno", selected[-1].lineno)
        later_nodes = [stmt for stmt in function.body if stmt.lineno > selected_end]
        later_facts = self._facts(later_nodes)

        function_facts = self._facts(list(function.body))
        parameters = {
            arg.arg
            for arg in (
                list(function.args.posonlyargs)
                + list(function.args.args)
                + list(function.args.kwonlyargs)
            )
        }
        if function.args.vararg:
            parameters.add(function.args.vararg.arg)
        if function.args.kwarg:
            parameters.add(function.args.kwarg.arg)
        locals_in_function = parameters | set(function_facts.stores)

        inputs: list[str] = []
        for name, load_pos in selected_facts.loads.items():
            store_pos = selected_facts.stores.get(name)
            needs_input = name in locals_in_function and (store_pos is None or load_pos <= store_pos)
            if needs_input and not (is_method and name in {"self", "cls"}):
                inputs.append(name)
        outputs = [name for name in selected_facts.stores if name in later_facts.loads]

        blocked = set(dir(builtins))
        inputs = sorted(dict.fromkeys(name for name in inputs if name not in blocked))
        outputs = sorted(dict.fromkeys(outputs))
        return ExtractMethodAnalysis(
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            is_method=is_method,
            is_async=isinstance(function, ast.AsyncFunctionDef),
        )

    @staticmethod
    def _rewrite(
        source: str,
        *,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        owner_class: ast.ClassDef | None,
        selected: list[ast.stmt],
        name: str,
        analysis: ExtractMethodAnalysis,
        preserve_loop_control: bool = False,
    ) -> str:
        lines = source.splitlines(keepends=True)
        first = selected[0].lineno - 1
        last = getattr(selected[-1], "end_lineno", selected[-1].lineno)
        original_indent = lines[first][: len(lines[first]) - len(lines[first].lstrip())]
        definition_indent = " " * int(getattr(function, "col_offset", 0))
        body_lines = lines[first:last]
        dedented = [line[len(original_indent):] if line.strip() else line for line in body_lines]
        has_loop_control = preserve_loop_control and any(
            isinstance(node, (ast.Break, ast.Continue))
            for statement in selected
            for node in ast.walk(statement)
        )
        if has_loop_control:
            selected_start = selected[0].lineno
            replacements: list[tuple[int, int, str]] = []
            for statement in selected:
                for node in ast.walk(statement):
                    if isinstance(node, ast.Break):
                        replacements.append((node.lineno - selected_start, node.col_offset, 'return "break"'))
                    elif isinstance(node, ast.Continue):
                        replacements.append((node.lineno - selected_start, node.col_offset, 'return "continue"'))
            for line_index, column, replacement_text in sorted(replacements, reverse=True):
                relative_column = max(0, column - len(original_indent))
                line = dedented[line_index]
                stripped_end = len(line.rstrip("\r\n"))
                newline = line[stripped_end:]
                dedented[line_index] = line[:relative_column] + replacement_text + newline

        receiver = ""
        call_target = name
        params = list(analysis.inputs)
        if owner_class is not None:
            first_arg = function.args.args[0].arg if function.args.args else "self"
            receiver = first_arg
            params.insert(0, receiver)
            call_target = f"{receiver}.{name}"

        async_prefix = "async " if analysis.is_async else ""
        await_prefix = "await " if analysis.is_async else ""
        new_def = [f"{definition_indent}{async_prefix}def {name}({', '.join(params)}):\n"]
        new_def.extend(f"{definition_indent}    {line}" if line.strip() else line for line in dedented)
        if analysis.outputs:
            returned = ", ".join(analysis.outputs)
            new_def.append(f"{definition_indent}    return {returned}\n")
        elif has_loop_control:
            new_def.append(f"{definition_indent}    return None\n")
        new_def.append("\n")

        args = ", ".join(analysis.inputs)
        call = f"{await_prefix}{call_target}({args})"
        if has_loop_control:
            replacement = [
                f"{original_indent}extract_action = {call}\n",
                f'{original_indent}if extract_action == "break":\n',
                f"{original_indent}    break\n",
                f'{original_indent}if extract_action == "continue":\n',
                f"{original_indent}    continue\n",
            ]
        elif analysis.outputs:
            lhs = ", ".join(analysis.outputs)
            replacement = [f"{original_indent}{lhs} = {call}\n"]
        else:
            replacement = [f"{original_indent}{call}\n"]

        insert_at = function.lineno - 1
        lines[first:last] = replacement
        if insert_at > first:
            insert_at -= (last - first) - len(replacement)
        lines[insert_at:insert_at] = new_def
        result = "".join(lines)
        ast.parse(result)
        return result
