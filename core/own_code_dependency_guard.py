"""Cross-file import and call compatibility checks for own-code proposals."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import tokenize


@dataclass(frozen=True, slots=True)
class DependencyGuardResult:
    valid: bool
    issues: tuple[str, ...]
    scanned_files: int

    def report(self) -> str:
        if self.valid:
            return f"Çapraz dosya uyumluluğu doğrulandı; {self.scanned_files} Python dosyası tarandı."
        return "Çapraz dosya uyumluluk hatası: " + "; ".join(self.issues)


def _public_functions(tree: ast.Module) -> dict[str, tuple[int, int | None]]:
    result: dict[str, tuple[int, int | None]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            args = node.args
            positional = len(args.posonlyargs) + len(args.args)
            required = positional - len(args.defaults)
            maximum = None if args.vararg is not None else positional
            result[node.name] = (required, maximum)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_"):
                    args = child.args
                    positional = max(0, len(args.posonlyargs) + len(args.args) - 1)
                    required = max(0, positional - len(args.defaults))
                    maximum = None if args.vararg is not None else positional
                    result[f"{node.name}.{child.name}"] = (required, maximum)
    return result


def _module_name(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    if normalized.endswith("/__init__"):
        normalized = normalized[:-9]
    return normalized.strip("/").replace("/", ".")


def validate_dependency_compatibility(
    root: str | Path,
    changes: object,
    *,
    max_files: int = 3000,
) -> DependencyGuardResult:
    project_root = Path(root).expanduser().resolve()
    overlay: dict[str, str] = {}
    changed_apis: dict[str, tuple[tuple[int, int | None] | None, tuple[int, int | None] | None, str]] = {}
    issues: list[str] = []
    for change in tuple(changes or ()):
        path = str(getattr(change, "path", "")).replace("\\", "/")
        if not path.casefold().endswith(".py"):
            continue
        old = str(getattr(change, "old_content", "") or "")
        new = str(getattr(change, "new_content", "") or "")
        overlay[path.casefold()] = new
        try:
            old_api = _public_functions(ast.parse(old or "", filename=path))
            new_api = _public_functions(ast.parse(new, filename=path))
        except SyntaxError:
            continue
        for name in set(old_api).union(new_api):
            if old_api.get(name) != new_api.get(name):
                changed_apis[name] = (old_api.get(name), new_api.get(name), path)
    if not changed_apis:
        return DependencyGuardResult(True, (), 0)

    scanned = 0
    skip_parts = {".git", ".venv", "venv", "__pycache__", ".artmach_assistant"}
    try:
        candidates = project_root.rglob("*.py")
    except OSError as exc:
        return DependencyGuardResult(False, (f"Kaynak ağacı taranamadı: {exc}",), 0)
    for target in candidates:
        if scanned >= max(1, min(int(max_files), 10000)):
            issues.append("Çapraz dosya tarama güvenlik sınırına ulaştı.")
            break
        try:
            relative = target.resolve().relative_to(project_root).as_posix()
        except (OSError, ValueError):
            continue
        if any(part in skip_parts for part in Path(relative).parts):
            continue
        try:
            source = overlay.get(relative.casefold())
            if source is None:
                if target.stat().st_size > 2_000_000:
                    continue
                with tokenize.open(target) as handle:
                    source = handle.read()
            tree = ast.parse(source, filename=relative)
        except (OSError, UnicodeError, SyntaxError):
            continue
        scanned += 1
        imported_bindings: dict[str, str] = {}
        variable_types: dict[str, str] = {}
        for item in ast.walk(tree):
            if isinstance(item, ast.ImportFrom):
                for alias in item.names:
                    for api_name, (_old, _new, owner_path) in changed_apis.items():
                        if alias.name != api_name.split(".")[-1]:
                            continue
                        owner_module = _module_name(owner_path)
                        imported_module = str(item.module or "")
                        if (
                            imported_module == owner_module
                            or owner_module.endswith("." + imported_module)
                            or imported_module.endswith("." + owner_module)
                        ):
                            imported_bindings[alias.asname or alias.name] = api_name
            elif (
                isinstance(item, ast.Assign)
                and isinstance(item.value, ast.Call)
                and isinstance(item.value.func, ast.Name)
            ):
                for assigned in item.targets:
                    if isinstance(assigned, ast.Name):
                        variable_types[assigned.id] = item.value.func.id
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_module = str(node.module or "")
                for alias in node.names:
                    for api_name, (old_sig, new_sig, owner_path) in changed_apis.items():
                        short = api_name.split(".")[-1]
                        owner_module = _module_name(owner_path)
                        if (
                            new_sig is None
                            and alias.name == short
                            and (
                                imported_module == owner_module
                                or owner_module.endswith("." + imported_module)
                                or imported_module.endswith("." + owner_module)
                            )
                        ):
                            issues.append(
                                f"{relative}:{node.lineno} kaldırılan {short} sembolünü import ediyor"
                            )
            if not isinstance(node, ast.Call):
                continue
            called = ""
            qualified_call = ""
            if isinstance(node.func, ast.Name):
                called = node.func.id
                qualified_call = imported_bindings.get(called, "")
            elif isinstance(node.func, ast.Attribute):
                called = node.func.attr
                if isinstance(node.func.value, ast.Name):
                    owner = variable_types.get(
                        node.func.value.id, node.func.value.id
                    )
                    qualified_call = f"{owner}.{called}"
            if not called:
                continue
            positional_count = len(node.args)
            for api_name, (_old_sig, new_sig, owner_path) in changed_apis.items():
                same_owner = relative.casefold() == owner_path.casefold()
                direct_import = imported_bindings.get(called) == api_name
                qualified_match = qualified_call == api_name
                if (
                    new_sig is None
                    or api_name.split(".")[-1] != called
                    or not (same_owner or direct_import or qualified_match)
                ):
                    continue
                required, maximum = new_sig
                if positional_count < required or (
                    maximum is not None and positional_count > maximum
                ):
                    expected = (
                        f"{required} veya daha fazla"
                        if maximum is None
                        else f"{required}-{maximum}"
                    )
                    issues.append(
                        f"{relative}:{node.lineno} {called} çağrısı {positional_count} "
                        f"konumsal argüman veriyor; yeni API {expected} bekliyor"
                    )
        if len(issues) >= 30:
            issues.append("Ek uyumsuzluklar güvenli rapor sınırında kesildi.")
            break
    return DependencyGuardResult(not issues, tuple(issues), scanned)
