from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class SymbolScopeResult:
    valid: bool
    reasons: tuple[str, ...]

    def report(self) -> str:
        if self.valid:
            return "Onayli sembol kapsami korundu."
        return "Onayli sembol kapsami ihlal edildi: " + "; ".join(self.reasons)


def _dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _module_parts(tree: ast.Module) -> dict[str, str]:
    result: dict[str, str] = {}
    other_index = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[f"function:{node.name}"] = _dump(node)
        elif isinstance(node, ast.ClassDef):
            class_shell = ast.ClassDef(
                name=node.name,
                bases=node.bases,
                keywords=node.keywords,
                body=[
                    child
                    for child in node.body
                    if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                ],
                decorator_list=node.decorator_list,
                type_params=getattr(node, "type_params", []),
            )
            result[f"class-shell:{node.name}"] = _dump(class_shell)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[f"method:{node.name}.{child.name}"] = _dump(child)
        else:
            result[f"module:{other_index}"] = _dump(node)
            other_index += 1
    return result


def _approved_keys(symbols: Iterable[str]) -> set[str]:
    keys: set[str] = set()
    for raw in symbols:
        symbol = str(raw or "").strip()
        if not symbol:
            continue
        parts = [part for part in symbol.split(".") if part]
        if len(parts) >= 2:
            keys.add(f"method:{parts[-2]}.{parts[-1]}")
        # Runtime locations can append an inner callable label to the actual
        # class method, for example TaskOrchestrator.wrap.execute.  When the
        # leading component is class-like, authorize the direct outer method
        # as well.  Lower-case module.Class.method paths keep the existing
        # last-two-components behavior and do not gain broader scope.
        if len(parts) >= 3 and parts[0][:1].isupper():
            keys.add(f"method:{parts[0]}.{parts[1]}")
        keys.add(f"function:{parts[-1]}")
        keys.add(f"class-shell:{parts[-1]}")
    return keys


def _called_private_companion_keys(
    old_tree: ast.Module,
    new_tree: ast.Module,
    approved: set[str],
) -> set[str]:
    """Authorize only new private sibling methods called by an approved method."""
    old_classes = {
        node.name: node for node in old_tree.body if isinstance(node, ast.ClassDef)
    }
    allowed: set[str] = set()
    for owner in new_tree.body:
        if not isinstance(owner, ast.ClassDef):
            continue
        old_owner = old_classes.get(owner.name)
        if old_owner is None:
            continue
        old_methods = {
            node.name
            for node in old_owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        new_private = {
            node.name
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_")
            and node.name not in old_methods
        }
        if not new_private:
            continue
        for method in owner.body:
            method_key = f"method:{owner.name}.{getattr(method, 'name', '')}"
            if method_key not in approved:
                continue
            called = {
                call.func.attr
                for call in ast.walk(method)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "self"
            }
            allowed.update(
                f"method:{owner.name}.{name}" for name in new_private & called
            )
    return allowed


def validate_approved_symbol_scope(
    changes: Iterable[object],
    approved_symbols: Iterable[str],
    *,
    allow_called_private_companions: bool = False,
) -> SymbolScopeResult:
    approved = _approved_keys(approved_symbols)
    if not approved:
        return SymbolScopeResult(True, ())
    reasons: list[str] = []
    for change in changes:
        path = str(getattr(change, "path", ""))
        if not path.casefold().endswith(".py"):
            continue
        old_content = getattr(change, "old_content", "")
        new_content = getattr(change, "new_content", "")
        try:
            old_tree = ast.parse(str(old_content), filename=path)
            new_tree = ast.parse(str(new_content), filename=path)
        except SyntaxError as exc:
            reasons.append(f"{path}:{exc.lineno or 0} [python_syntax] {exc.msg}")
            continue
        old_parts = _module_parts(old_tree)
        new_parts = _module_parts(new_tree)
        locally_approved = set(approved)
        if allow_called_private_companions:
            locally_approved.update(
                _called_private_companion_keys(old_tree, new_tree, approved)
            )
        all_keys = sorted(set(old_parts) | set(new_parts))
        for key in all_keys:
            if key in locally_approved:
                continue
            if old_parts.get(key) != new_parts.get(key):
                human = key.split(":", 1)[-1]
                reasons.append(
                    f"{path} [symbol_scope] onay disi sembol degisti: {human}"
                )
                if len(reasons) >= 12:
                    return SymbolScopeResult(False, tuple(reasons))
    return SymbolScopeResult(not reasons, tuple(reasons))
