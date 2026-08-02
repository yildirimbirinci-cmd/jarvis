"""Conservative semantic checks for model-generated full-file replacements."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import difflib
import unicodedata


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch)).replace("ı", "i")


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int, int, int, bool, bool]:
    args = node.args
    positional = len(args.posonlyargs) + len(args.args)
    return (
        positional,
        len(args.kwonlyargs),
        len(args.defaults),
        sum(value is not None for value in args.kw_defaults),
        args.vararg is not None,
        args.kwarg is not None,
    )


def _public_symbols(tree: ast.Module) -> dict[str, tuple[str, object]]:
    result: dict[str, tuple[str, object]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            result[node.name] = ("function", _signature(node))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            result[node.name] = ("class", tuple(base.id for base in node.bases if isinstance(base, ast.Name)))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_"):
                    result[f"{node.name}.{child.name}"] = ("method", _signature(child))
    return result


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    value: ast.AST = node.func
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _behavior_inventory(tree: ast.Module) -> dict[str, int]:
    inventory: dict[str, int] = {}
    for node in ast.walk(tree):
        key = ""
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name:
                key = "call:" + name
        elif isinstance(node, ast.Break):
            key = "control:break"
        elif isinstance(node, ast.Continue):
            key = "control:continue"
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    assign_key = f"assign:{target.value.id}.{target.attr}"
                    inventory[assign_key] = inventory.get(assign_key, 0) + 1
        if key:
            inventory[key] = inventory.get(key, 0) + 1
    return inventory


@dataclass(frozen=True, slots=True)
class SemanticGuardResult:
    valid: bool
    issues: tuple[str, ...]

    def report(self) -> str:
        return (
            "Semantik koruma doğrulandı."
            if self.valid
            else "Semantik koruma reddi: " + "; ".join(self.issues)
        )


def validate_semantic_replacement(
    instruction: str,
    changes: object,
) -> SemanticGuardResult:
    normalized_instruction = _normalize(instruction)
    allows_removal = any(
        word in normalized_instruction
        for word in ("kaldir", "sil", "remove", "delete", "temizle")
    )
    allows_api_change = any(
        word in normalized_instruction
        for word in ("imza", "parametre", "arguman", "api", "signature")
    )
    allows_rewrite = any(
        word in normalized_instruction
        for word in ("yeniden yaz", "donustur", "refaktor", "mimariyi degistir")
    )
    preserves_behavior = (
        any(phrase in normalized_instruction for phrase in (
            "davranisi degistirmeden", "davranisi koru", "behavior-preserving",
        ))
        and any(word in normalized_instruction for word in (
            "refaktor", "cikar", "ayir", "extract",
        ))
    )
    issues: list[str] = []
    for change in tuple(changes or ()):
        path = str(getattr(change, "path", ""))
        if not path.casefold().endswith(".py"):
            continue
        old = str(getattr(change, "old_content", "") or "")
        new = str(getattr(change, "new_content", "") or "")
        if not old.strip():
            continue
        try:
            old_tree = ast.parse(old, filename=path)
            new_tree = ast.parse(new, filename=path)
        except SyntaxError:
            # PatchValidator owns syntax diagnostics.
            continue
        old_symbols = _public_symbols(old_tree)
        new_symbols = _public_symbols(new_tree)
        if preserves_behavior:
            old_behavior = _behavior_inventory(old_tree)
            new_behavior = _behavior_inventory(new_tree)
            lost = sorted(
                key for key, count in old_behavior.items()
                if new_behavior.get(key, 0) < count
            )
            if lost:
                issues.append(
                    f"{path}: davranış-koruyan refaktörde gözlenebilir işlem kaybı "
                    f"({', '.join(lost[:10])})"
                )
        removed = sorted(set(old_symbols).difference(new_symbols))
        if removed and not allows_removal:
            issues.append(
                f"{path}: beklenmeyen genel sembol kaybı ({', '.join(removed[:8])})"
            )
        if not allows_api_change:
            changed_signatures = sorted(
                name for name in set(old_symbols).intersection(new_symbols)
                if old_symbols[name][0] in {"function", "method"}
                and old_symbols[name] != new_symbols[name]
            )
            if changed_signatures:
                issues.append(
                    f"{path}: beklenmeyen genel API imzası değişikliği "
                    f"({', '.join(changed_signatures[:8])})"
                )
        old_lines = old.splitlines()
        new_lines = new.splitlines()
        if len(old_lines) >= 100:
            similarity = difflib.SequenceMatcher(
                None, old_lines, new_lines, autojunk=False
            ).ratio()
            if similarity < 0.55 and not allows_rewrite:
                issues.append(
                    f"{path}: dosyanın büyük bölümü plan dışı yeniden yazılıyor "
                    f"(benzerlik %{int(similarity * 100)})"
                )
        if (
            len(old_lines) >= 40
            and len(new_lines) < len(old_lines) * 0.65
            and not allows_removal
        ):
            issues.append(
                f"{path}: beklenmeyen geniş içerik kaybı "
                f"({len(old_lines)} satırdan {len(new_lines)} satıra)"
            )
    return SemanticGuardResult(not issues, tuple(issues))
