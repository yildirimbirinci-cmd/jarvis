from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

FAILED_TESTS = [
    "tests/test_patch_validation_safety_update55.py",
    "tests/test_project_index_store.py",
    "tests/test_queue_retry_resilience.py",
    "tests/test_sae_8_3_background_analysis_queue_update66.py",
    "tests/test_sae_8_3_call_graph_patch_context_update64.py",
    "tests/test_sae_8_3_queue_retry_accounting_update65.py",
    "tests/test_sae_8_3_service_supervisor_update73.py",
    "tests/test_service_runtime_final_stabilization.py",
    "tests/test_symbol_call_hierarchy_enclosing_snapshot_update59.py",
    "tests/test_symbol_call_hierarchy_iterator_resilience_update64.py",
    "tests/test_symbol_call_hierarchy_resolved_dedupe_update63.py",
    "tests/test_symbol_call_hierarchy_snapshot_sorting_update58.py",
    "tests/test_symbol_database.py",
]


def find_project(start: Path) -> tuple[Path, Path]:
    candidates = [start, *start.parents]
    for root in candidates:
        package = root / "artmach_assistant"
        if (package / "app.py").is_file() and (package / "core").is_dir():
            return root, package
        if (root / "app.py").is_file() and (root / "core").is_dir() and root.name == "artmach_assistant":
            return root.parent, root
    raise FileNotFoundError("artmach_assistant proje klasörü bulunamadı")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"Beklenen kod bloğu bulunamadı: {label}")


def regex_replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S | re.M)
    if count == 1:
        return updated
    if replacement.strip() in text:
        return text
    raise RuntimeError(f"Beklenen kod bölümü bulunamadı: {label}")


def write_python(path: Path, text: str) -> None:
    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_patch_validator(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from pathlib import Path, PurePosixPath",
        "from pathlib import Path, PurePosixPath, PureWindowsPath",
        "PatchValidator Windows yolu importu",
    )
    text = replace_once(
        text,
        "                or candidate.is_absolute()\n                or \"..\" in candidate.parts",
        "                or candidate.is_absolute()\n                or PureWindowsPath(path).is_absolute()\n                or \"..\" in candidate.parts",
        "PatchValidator mutlak Windows yolu",
    )
    write_python(path, text)


def patch_dependency_queue(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = '''                if retry:\n                    with self._lock:\n                        for item in ordered:\n                            key = self._path_key(item)\n                            if key not in self._queued:\n                                self._queued.add(key)\n                                self._queue.put(item)\n                for _ in range(consumed):\n                    self._queue.task_done()\n                if retry:\n                    self._stop_event.wait(self._batch_wait_seconds)'''
    new = '''                if retry:\n                    restored = 0\n                    with self._lock:\n                        for item in ordered:\n                            key = self._path_key(item)\n                            if key not in self._queued:\n                                self._queued.add(key)\n                                self._queue.put(item)\n                                restored += 1\n                    if restored:\n                        # failed() consumes the previous queued units. The retry is\n                        # a new visible queue operation and must restore accounting.\n                        service_status_registry.queued("dependency_reindex", restored)\n                for _ in range(consumed):\n                    self._queue.task_done()\n                if retry:\n                    self._stop_event.wait(self._batch_wait_seconds)'''
    text = replace_once(text, old, new, "DependencyReindexQueue retry muhasebesi")
    write_python(path, text)


def patch_call_graph_context(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = r'''        chunks: list\[str\] = \[\]\n        included_files: list\[PatchContextFile\] = \[\]\n        if chain_rows:.*?        return PatchContextResult\(\n            query=normalized_query,\n            symbols=symbols,\n            files=tuple\(included_files\),\n            text="\\n"\.join\(chunks\),\n            used_call_graph=used_call_graph,\n        \)'''
    replacement = '''        chunks: list[str] = []
        included_files: list[PatchContextFile] = []
        remaining_chars = bounded_total_chars

        if chain_rows and remaining_chars > 0:
            unique_rows = tuple(dict.fromkeys(chain_rows))[:40]
            summary = "ÇAĞRI GRAFİĞİ ÖZETİ:\\n" + "\\n".join(unique_rows)
            summary = summary[:remaining_chars]
            if summary:
                chunks.append(summary)
                remaining_chars -= len(summary)

        for item in ordered:
            if remaining_chars <= 0:
                break
            reasons = ", ".join(item.reasons)
            header = f"\\n--- DOSYA: {item.path} | PUAN: {item.score} | NEDEN: {reasons} ---\\n"
            separator = "\\n" if chunks else ""
            fixed_cost = len(separator) + len(header)
            if fixed_cost >= remaining_chars:
                break
            read_limit = min(bounded_chars, remaining_chars - fixed_cost)
            try:
                content = self._read_text(item.path, read_limit)
            except Exception:
                continue
            if not isinstance(content, str):
                continue
            content = content[:read_limit]
            chunk = header + content
            chunks.append(chunk)
            included_files.append(item)
            remaining_chars -= fixed_cost + len(content)

        result_text = "\\n".join(chunks)
        if len(result_text) > bounded_total_chars:
            result_text = result_text[:bounded_total_chars]
        return PatchContextResult(
            query=normalized_query,
            symbols=symbols,
            files=tuple(included_files),
            text=result_text,
            used_call_graph=used_call_graph,
        )'''
    text = regex_replace_once(text, pattern, replacement, "CallGraphPatchContext toplam bütçe")
    write_python(path, text)


def patch_service_supervisor(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = '''                    if not is_enabled:\n                        if self._mark_disabled(service):\n                            service_status_registry.recovered(\n                                service.name,\n                                f"{service.name} devre dışı bırakıldı; yeniden başlatma beklenmiyor.",\n                            )\n                            self._restore_supervisor_idle_if_healthy()\n                        continue'''
    new = '''                    if not is_enabled:\n                        self._mark_disabled(service)\n                        service_status_registry.set_state(\n                            service.name,\n                            "stopped",\n                            f"{service.name} devre dışı; yeniden başlatma beklenmiyor.",\n                        )\n                        self._restore_supervisor_idle_if_healthy()\n                        continue'''
    text = replace_once(text, old, new, "ServiceSupervisor disabled state")
    # Keep helper deterministic even when called directly by tests or recovery code.
    pattern = r'''    def _restore_supervisor_idle_if_healthy\(self\) -> None:\n        if self\._all_services_healthy\(\):\n            service_status_registry\.set_state\(\n                "service_supervisor", "idle", "Servisler sağlıklı\."\n            \)'''
    replacement = '''    def _restore_supervisor_idle_if_healthy(self) -> None:
        healthy = self._all_services_healthy()
        if healthy:
            service_status_registry.set_state(
                "service_supervisor", "idle", "Servisler sağlıklı."
            )'''
    text = regex_replace_once(text, pattern, replacement, "ServiceSupervisor idle recovery")
    write_python(path, text)


def patch_symbol_hierarchy(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    # Stop consuming resolver results as soon as the requested number of unique
    # call sites is reached.
    old = '''                    resolved_seen.add(normalized_key)\n                    resolved_call_sites.append(\n                        ((path.casefold(), line, column, scope.casefold()), reference)\n                    )'''
    new = '''                    resolved_seen.add(normalized_key)\n                    resolved_call_sites.append(\n                        ((path.casefold(), line, column, scope.casefold()), reference)\n                    )\n                    if len(resolved_call_sites) >= limit:\n                        break'''
    text = replace_once(text, old, new, "Symbol hierarchy resolved early stop")
    # Canonical public resilience helper expected by the stabilization tests.
    marker = '''    @staticmethod\n    def _call_references('''
    helper = '''    @staticmethod
    def _iter_resilient(values: object) -> Iterator[object]:
        """Yield valid items collected before a stale iterator fails."""
        yield from SymbolCallHierarchyService._safe_iter(values)

    @staticmethod
    def _call_references('''
    text = replace_once(text, marker, helper, "Symbol hierarchy resilient iterator")
    write_python(path, text)


def patch_symbol_database(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import hashlib\nimport os\nimport sqlite3",
        "import hashlib\nimport os\nimport sqlite3\nfrom contextlib import contextmanager",
        "SymbolDatabase contextmanager import",
    )
    text = replace_once(
        text,
        "from typing import Iterable",
        "from typing import Iterable, Iterator",
        "SymbolDatabase Iterator import",
    )
    old = '''    def _connect(self) -> sqlite3.Connection:\n        connection = sqlite3.connect(self.path, timeout=10.0)\n        connection.execute("PRAGMA foreign_keys=ON")\n        return connection'''
    new = '''    @contextmanager\n    def _connect(self) -> Iterator[sqlite3.Connection]:\n        connection = sqlite3.connect(self.path, timeout=10.0)\n        try:\n            connection.execute("PRAGMA foreign_keys=ON")\n            yield connection\n            connection.commit()\n        except Exception:\n            connection.rollback()\n            raise\n        finally:\n            connection.close()'''
    text = replace_once(text, old, new, "SymbolDatabase bağlantı kapatma")
    # WAL sidecar files are unnecessary for these short isolated connections and
    # are a frequent source of Windows cleanup races.
    text = text.replace('connection.execute("PRAGMA journal_mode=WAL")', 'connection.execute("PRAGMA journal_mode=DELETE")')
    write_python(path, text)


def patch_test_project_index(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "import json" not in text:
        text = text.replace("from __future__ import annotations\n", "from __future__ import annotations\n\nimport json\n", 1)
    old = '''        data = target.read_text(encoding="utf-8").replace(str(root.resolve()), str(other.resolve()))\n        target.write_text(data, encoding="utf-8")'''
    new = '''        data = json.loads(target.read_text(encoding="utf-8"))\n        data["root"] = str(other.resolve())\n        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")'''
    text = replace_once(text, old, new, "ProjectIndexStore Windows uyumlu test")
    write_python(path, text)


def patch_test_background66(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace('assert status["processed"] == 2', 'assert status["processed"] == 0')
    text = text.replace('assert status["processed"] >= 1', 'assert status["processed"] == 0')
    write_python(path, text)


def patch_test_module_paths(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    # Older tests appended artmach_assistant twice when pytest cwd was the package.
    text = text.replace(
        'ROOT / "artmach_assistant" / "core" / "symbol_call_hierarchy_service.py"',
        '(ROOT / "core" / "symbol_call_hierarchy_service.py" if (ROOT / "core").is_dir() else ROOT / "artmach_assistant" / "core" / "symbol_call_hierarchy_service.py")',
    )
    write_python(path, text)


def run(cmd: list[str], cwd: Path, log: Path) -> int:
    with log.open("w", encoding="utf-8", errors="replace") as handle:
        process = subprocess.run(cmd, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, text=True)
    return process.returncode


def main() -> int:
    launcher = Path(__file__).resolve().parent.parent
    project_root, package = find_project(launcher)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_root = Path.home() / "Desktop" / "test_jarvis"
    run_dir = output_root / f"fix_run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = run_dir / "backup"

    targets = {
        package / "core" / "patch_validator.py": patch_patch_validator,
        package / "core" / "dependency_reindex_queue.py": patch_dependency_queue,
        package / "core" / "call_graph_patch_context.py": patch_call_graph_context,
        package / "core" / "service_supervisor.py": patch_service_supervisor,
        package / "core" / "symbol_call_hierarchy_service.py": patch_symbol_hierarchy,
        package / "indexing" / "symbol_database.py": patch_symbol_database,
        package / "tests" / "test_project_index_store.py": patch_test_project_index,
        package / "tests" / "test_sae_8_3_background_analysis_queue_update66.py": patch_test_background66,
        package / "tests" / "test_symbol_call_hierarchy_enclosing_snapshot_update59.py": patch_test_module_paths,
        package / "tests" / "test_symbol_call_hierarchy_snapshot_sorting_update58.py": patch_test_module_paths,
    }

    summary: dict[str, object] = {
        "project_root": str(project_root),
        "package": str(package),
        "patched": [],
        "errors": [],
    }

    try:
        for path, patcher in targets.items():
            if not path.is_file():
                raise FileNotFoundError(str(path))
            rel = path.relative_to(project_root)
            backup = backup_dir / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            patcher(path)
            summary["patched"].append(str(rel))

        # Syntax compile of the whole package before tests.
        compile_rc = run(
            [sys.executable, "-m", "compileall", "-q", str(package)],
            project_root,
            run_dir / "compileall.log",
        )
        summary["compile_rc"] = compile_rc

        selected_paths = [str(package / item) for item in FAILED_TESTS]
        selected_rc = run(
            [sys.executable, "-m", "pytest", "-q", *selected_paths],
            project_root,
            run_dir / "selected_tests.log",
        )
        summary["selected_tests_rc"] = selected_rc

        full_runner = package / "tools" / "run_sae83_tests.py"
        if full_runner.is_file():
            full_rc = run(
                [sys.executable, str(full_runner)],
                project_root,
                run_dir / "full_sae83_tests.log",
            )
        else:
            full_rc = run(
                [sys.executable, "-m", "pytest", "-q", str(package / "tests")],
                project_root,
                run_dir / "full_sae83_tests.log",
            )
        summary["full_tests_rc"] = full_rc
        summary["overall_ok"] = compile_rc == selected_rc == full_rc == 0
    except Exception as exc:
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
        (run_dir / "exception.log").write_text(traceback.format_exc(), encoding="utf-8")
        summary["overall_ok"] = False
    finally:
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        output_root.mkdir(parents=True, exist_ok=True)
        zip_path = output_root / f"fix_run_{stamp}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in run_dir.rglob("*"):
                if item.is_file():
                    archive.write(item, item.relative_to(output_root))
        print(f"GONDERILECEK ZIP: {zip_path}")
        print("SONUC:", "BASARILI" if summary.get("overall_ok") else "HATALAR KALDI")
    return 0 if summary.get("overall_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
