from __future__ import annotations

import argparse
import ast
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


IMPORT_LINE = (
    "from artmach_assistant.core.end_to_end_acceptance_ui "
    "import install_main_window_end_to_end_acceptance"
)
INSTALL_LINE = "install_main_window_end_to_end_acceptance(MainWindow)"
MARKER = "# Jarvis end-to-end acceptance UI integration."
REQUIRED_METHODS: set[str] = set()
REQUIRED_FILES = (
    "core/end_to_end_acceptance.py",
    "core/end_to_end_acceptance_ui.py",
)


@dataclass(frozen=True, slots=True)
class Inspection:
    app_path: Path
    installed: bool
    missing_methods: tuple[str, ...]
    missing_files: tuple[str, ...]

    @property
    def compatible(self) -> bool:
        return not self.missing_methods and not self.missing_files


def _app_path(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    direct = root / "app.py"
    nested = root / "artmach_assistant" / "app.py"
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested
    raise FileNotFoundError(
        "app.py bulunamadi. --project-root ile artmach_assistant klasorunu "
        "veya onun ust klasorunu belirt."
    )


def _parse(source: str, path: Path) -> ast.Module:
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise RuntimeError(
            f"app.py sozdizimi gecersiz; entegrasyon uygulanmadi: {exc}"
        ) from exc


def _main_window(tree: ast.Module) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise RuntimeError("app.py icinde MainWindow sinifi bulunamadi.")


def inspect(project_root: Path) -> Inspection:
    app_path = _app_path(project_root)
    source = app_path.read_text(encoding="utf-8")
    tree = _parse(source, app_path)
    window = _main_window(tree)
    methods = {
        node.name
        for node in window.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing_methods = tuple(sorted(REQUIRED_METHODS - methods))
    missing_files = tuple(
        relative
        for relative in REQUIRED_FILES
        if not (app_path.parent / relative).is_file()
    )
    return Inspection(
        app_path=app_path,
        installed=IMPORT_LINE in source and INSTALL_LINE in source,
        missing_methods=missing_methods,
        missing_files=missing_files,
    )


def _line_offset(lines: list[str], line_number: int) -> int:
    return sum(len(line) for line in lines[: max(0, line_number - 1)])


def _patched_source(source: str, app_path: Path) -> str:
    tree = _parse(source, app_path)
    window = _main_window(tree)
    methods = {
        node.name
        for node in window.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = sorted(REQUIRED_METHODS - methods)
    if missing:
        raise RuntimeError(
            "app.py beklenen GUI yontemlerini icermiyor; hicbir dosya degistirilmedi. Eksik: "
            + ", ".join(missing)
        )
    newline = "\r\n" if "\r\n" in source else "\n"
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, str]] = []
    if IMPORT_LINE not in source:
        imports = [
            node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        if not imports:
            raise RuntimeError("app.py import bolumu bulunamadi.")
        last_line = max(int(node.end_lineno or node.lineno) for node in imports)
        edits.append((_line_offset(lines, last_line + 1), IMPORT_LINE + newline))
    if INSTALL_LINE not in source:
        main_node = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "main"
            ),
            None,
        )
        if main_node is None:
            raise RuntimeError("app.py icinde top-level main() bulunamadi.")
        edits.append(
            (
                _line_offset(lines, main_node.lineno),
                f"{newline}{MARKER}{newline}{INSTALL_LINE}{newline}{newline}",
            )
        )
    patched = source
    for offset, value in sorted(edits, reverse=True):
        patched = patched[:offset] + value + patched[offset:]
    _parse(patched, app_path)
    if IMPORT_LINE not in patched or INSTALL_LINE not in patched:
        raise RuntimeError("Kabul testi arayuz entegrasyonu dogrulanamadi.")
    return patched


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _atomic_write(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _backup_path(app_path: Path) -> Path:
    return app_path.parent / ".jarvis_backups" / "app.py.before_end_to_end_acceptance_ui"


def apply(project_root: Path) -> Inspection:
    before = inspect(project_root)
    if not before.compatible:
        details = [
            *(f"app.py yontemi: {item}" for item in before.missing_methods),
            *(f"dosya: {item}" for item in before.missing_files),
        ]
        raise RuntimeError(
            "Kabul testi arayuzu guvenli bicimde uygulanamadi. Eksikler: "
            + "; ".join(details)
        )
    if before.installed:
        return before
    original_bytes = before.app_path.read_bytes()
    original = original_bytes.decode("utf-8")
    patched = _patched_source(original, before.app_path)
    backup = _backup_path(before.app_path)
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(before.app_path, backup)
    _atomic_write(before.app_path, patched)
    after = inspect(project_root)
    if not after.installed:
        _atomic_write_bytes(before.app_path, original_bytes)
        raise RuntimeError("Son dogrulama basarisiz; app.py geri alindi.")
    return after


def revert(project_root: Path) -> Path:
    app_path = _app_path(project_root)
    backup = _backup_path(app_path)
    if not backup.is_file():
        raise FileNotFoundError(f"Geri alma yedegi bulunamadi: {backup}")
    payload = backup.read_bytes()
    source = payload.decode("utf-8")
    _parse(source, app_path)
    _atomic_write_bytes(app_path, payload)
    return app_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Jarvis kabul ve stabilizasyon sekmesini app.py uzerine guvenli ekler."
    )
    parser.add_argument("--project-root", default=".")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--revert", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = Path(args.project_root)
    try:
        if args.revert:
            path = revert(root)
            print(f"geri alindi: {path}")
            return 0
        result = apply(root) if args.apply else inspect(root)
        print(f"app.py: {result.app_path}")
        print(f"uyumlu: {'evet' if result.compatible else 'hayir'}")
        print(f"kurulu: {'evet' if result.installed else 'hayir'}")
        if result.missing_methods:
            print("eksik yontemler: " + ", ".join(result.missing_methods))
        if result.missing_files:
            print("eksik dosyalar: " + ", ".join(result.missing_files))
        return 0 if result.compatible else 2
    except Exception as exc:
        print(f"HATA: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
