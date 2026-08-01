from __future__ import annotations

import argparse
import ast
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


IMPORT_LINE = (
    "from artmach_assistant.core.gui_voice_integration "
    "import install_main_window_voice_integration"
)
INSTALL_LINE = "install_main_window_voice_integration(MainWindow)"
MARKER = "# Jarvis turn-aware voice integration; keeps the current app.py intact."
REQUIRED_METHODS = {
    # The installer reads these existing methods to discover the current Qt
    # Worker/QTimer/BargeInWorker classes and to preserve MainWindow.__init__.
    # Every other conversation method is supplied by the integration itself,
    # so older app.py revisions are not rejected merely because a new callback
    # did not exist yet.
    "run_worker",
    "on_answer",
    "_start_barge_in",
}

RUNTIME_CONTRACTS = {
    "core/assistant.py": {
        "AssistantEngine": {
            "handle": {"turn_id"},
            "response_packet": {"turn_id"},
        },
    },
    "core/conversation_runtime.py": {
        "ConversationRuntime": {
            "token_for": {"turn_id"},
            "is_current": {"turn_id"},
            "raise_if_cancelled": {"turn_id"},
            "begin_task": {"turn_id", "cancellation"},
            "response_ready": {"turn_id"},
            "packet_for": {"turn_id"},
            "mark_speaking": {"turn_id", "cancel_callback"},
            "complete": {"turn_id"},
            "cancel": {"turn_id"},
        },
    },
    "core/task_orchestrator.py": {
        "TaskOrchestrator": {
            "start": {"parent_token", "turn_id"},
        },
    },
    "core/voice_service.py": {
        "VoiceService": {
            "begin_speech_session": set(),
            "stop_speaking": {"session_id"},
            "speak": {"speech_session_id", "cancel_check"},
        },
    },
}


@dataclass(frozen=True, slots=True)
class Inspection:
    app_path: Path
    installed: bool
    import_present: bool
    install_call_present: bool
    missing_methods: tuple[str, ...]
    missing_contracts: tuple[str, ...] = ()

    @property
    def compatible(self) -> bool:
        return not self.missing_methods and not self.missing_contracts


def _app_path(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    direct = root / "app.py"
    nested = root / "artmach_assistant" / "app.py"
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested
    raise FileNotFoundError(
        "app.py bulunamadı. --project-root ile artmach_assistant klasörünü "
        "veya onun üst klasörünü belirt."
    )


def _parse(source: str, path: Path) -> ast.Module:
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise RuntimeError(
            f"app.py sözdizimi geçersiz; entegrasyon uygulanmadı: {exc}"
        ) from exc


def _main_window(tree: ast.Module) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise RuntimeError("app.py içinde MainWindow sınıfı bulunamadı.")


def _runtime_contract_issues(project_root: Path) -> tuple[str, ...]:
    issues: list[str] = []
    for relative, classes in RUNTIME_CONTRACTS.items():
        path = project_root / relative
        if not path.is_file():
            issues.append(f"{relative}: dosya yok")
            continue
        try:
            tree = _parse(path.read_text(encoding="utf-8"), path)
        except (OSError, RuntimeError) as exc:
            issues.append(f"{relative}: okunamadı ({exc})")
            continue
        class_nodes = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        for class_name, methods in classes.items():
            class_node = class_nodes.get(class_name)
            if class_node is None:
                issues.append(f"{relative}:{class_name} yok")
                continue
            method_nodes = {
                node.name: node
                for node in class_node.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for method_name, required_parameters in methods.items():
                method = method_nodes.get(method_name)
                if method is None:
                    issues.append(f"{relative}:{class_name}.{method_name} yok")
                    continue
                parameters = {
                    argument.arg
                    for argument in (
                        list(method.args.posonlyargs)
                        + list(method.args.args)
                        + list(method.args.kwonlyargs)
                    )
                }
                missing = sorted(set(required_parameters) - parameters)
                if missing:
                    issues.append(
                        f"{relative}:{class_name}.{method_name} eksik parametre "
                        + ",".join(missing)
                    )
    return tuple(sorted(issues))


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
    missing = tuple(sorted(REQUIRED_METHODS - methods))
    missing_contracts = _runtime_contract_issues(app_path.parent)
    import_present = IMPORT_LINE in source
    call_present = INSTALL_LINE in source
    return Inspection(
        app_path=app_path,
        installed=(
            import_present
            and call_present
            and not missing
            and not missing_contracts
        ),
        import_present=import_present,
        install_call_present=call_present,
        missing_methods=missing,
        missing_contracts=missing_contracts,
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
            "app.py beklenen GUI yöntemlerini içermiyor; hiçbir dosya "
            "değiştirilmedi. Eksik: " + ", ".join(missing)
        )

    newline = "\r\n" if "\r\n" in source else "\n"
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, str]] = []
    if IMPORT_LINE not in source:
        import_nodes = [
            node
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        if not import_nodes:
            raise RuntimeError("app.py import bölümü bulunamadı.")
        last_import_line = max(int(node.end_lineno or node.lineno) for node in import_nodes)
        edits.append((_line_offset(lines, last_import_line + 1), IMPORT_LINE + newline))

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
            raise RuntimeError("app.py içinde top-level main() bulunamadı.")
        insertion = f"{newline}{MARKER}{newline}{INSTALL_LINE}{newline}{newline}"
        edits.append((_line_offset(lines, main_node.lineno), insertion))

    patched = source
    for offset, text in sorted(edits, reverse=True):
        patched = patched[:offset] + text + patched[offset:]
    _parse(patched, app_path)
    if IMPORT_LINE not in patched or INSTALL_LINE not in patched:
        raise RuntimeError("Entegrasyon doğrulaması başarısız; app.py yazılmadı.")
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
    return app_path.parent / ".jarvis_backups" / "app.py.before_voice_gui_integration"


def apply(project_root: Path) -> Inspection:
    before = inspect(project_root)
    if not before.compatible:
        details = [
            *(f"app.py yöntemi: {row}" for row in before.missing_methods),
            *(f"çekirdek sözleşmesi: {row}" for row in before.missing_contracts),
        ]
        raise RuntimeError(
            "Ses entegrasyonu güvenli biçimde uygulanamadı. Eksikler: "
            + "; ".join(details)
        )
    integration_module = before.app_path.parent / "core" / "gui_voice_integration.py"
    coordinator_module = before.app_path.parent / "core" / "voice_turn_coordinator.py"
    missing_files = [
        str(path.relative_to(before.app_path.parent))
        for path in (integration_module, coordinator_module)
        if not path.is_file()
    ]
    if missing_files:
        raise RuntimeError(
            "Önce ZIP içindeki core dosyalarını proje üzerine kopyala. Eksik: "
            + ", ".join(missing_files)
        )
    if before.installed:
        return before
    original_bytes = before.app_path.read_bytes()
    source = original_bytes.decode("utf-8")
    patched = _patched_source(source, before.app_path)
    backup = _backup_path(before.app_path)
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(before.app_path, backup)
    _atomic_write(before.app_path, patched)
    after = inspect(project_root)
    if not after.installed:
        # Fail closed: restore the exact source that existed before this run.
        _atomic_write_bytes(before.app_path, original_bytes)
        raise RuntimeError("Entegrasyon son doğrulamadan geçmedi; app.py geri alındı.")
    return after


def revert(project_root: Path) -> Path:
    app_path = _app_path(project_root)
    backup = _backup_path(app_path)
    if not backup.is_file():
        raise FileNotFoundError(
            f"Geri alma yedeği bulunamadı: {backup}"
        )
    payload = backup.read_bytes()
    source = payload.decode("utf-8")
    _parse(source, app_path)
    _atomic_write_bytes(app_path, payload)
    return app_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Güncel app.py dosyasını ezmeden Jarvis kesilebilir ses GUI "
            "entegrasyonunu kurar."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="app.py dosyasını içeren artmach_assistant klasörü veya üst klasörü",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="yalnızca uyumluluğu denetle")
    action.add_argument("--apply", action="store_true", help="entegrasyonu atomik olarak uygula")
    action.add_argument("--revert", action="store_true", help="oluşturulan yedekten geri dön")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.revert:
            path = revert(args.project_root)
            print(f"Geri alındı: {path}")
            return 0
        result = apply(args.project_root) if args.apply else inspect(args.project_root)
    except Exception as exc:
        print(f"HATA: {exc}")
        return 2
    print(f"app.py: {result.app_path}")
    print(f"uyumlu: {'evet' if result.compatible else 'hayır'}")
    print(f"kurulu: {'evet' if result.installed else 'hayır'}")
    if result.missing_methods:
        print("eksik app.py yöntemleri: " + ", ".join(result.missing_methods))
    if result.missing_contracts:
        print("eksik çekirdek sözleşmeleri:")
        for issue in result.missing_contracts:
            print("- " + issue)
    return 0 if result.compatible else 1


if __name__ == "__main__":
    raise SystemExit(main())
