from __future__ import annotations

import ast
import hashlib
import json
import os
import threading
import time
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from artmach_assistant.core.background_analysis_queue import BackgroundAnalysisQueue
from artmach_assistant.core.constitution import ModuleConstitutionContext, RuntimePolicy

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.project_index import IGNORED_DIRS
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

SAE_DIR = DATA_DIR / "sae"
SAE_INDEX_FILE = SAE_DIR / "self_index.json"
SAE_HISTORY_FILE = SAE_DIR / "scan_history.jsonl"
SAE_STATE_FILE = SAE_DIR / "runtime_state.json"
SAE_DEEP_REPORT_FILE = SAE_DIR / "deep_analysis.json"
SAE_APPROVAL_POLICY_FILE = SAE_DIR / "approval_policy.json"

SAE_STATE_MAX_BYTES = 256 * 1024
SAE_INDEX_MAX_BYTES = 32 * 1024 * 1024
SAE_DEEP_REPORT_MAX_BYTES = 8 * 1024 * 1024


@dataclass
class SymbolRecord:
    name: str
    kind: str
    line: int
    end_line: int
    parent: str = ""


@dataclass
class FileRecord:
    path: str
    size: int
    sha256: str
    modified_ns: int
    imports: list[str] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)
    syntax_error: str = ""


class SelfAwarenessEngine:
    """Jarvis'in kaynaklarını salt-okunur biçimde indeksleyen SAE çekirdeği.

    Otomatik tarama ve analiz yapabilir; kaynak kodu yazma/değiştirme yetkisi yoktur.
    Kod değişiklikleri mevcut EditManager onay akışı üzerinden yürütülmelidir.
    """

    VERSION = 2

    def __init__(
        self, project_root: Path, poll_seconds: float = 2.0, idle_seconds: float = 90.0,
        constitution: ModuleConstitutionContext | None = None,
    ) -> None:
        if constitution is None:
            raise RuntimeError(
                "SelfAwarenessEngine Constitution baglami olmadan baslatilamaz."
            )
        required_articles = {"1.5", "1.6", "1.9", "1.10"}
        missing = required_articles.difference(constitution.article_ids)
        if missing:
            raise RuntimeError(
                "SelfAwarenessEngine Constitution baglaminda zorunlu maddeler eksik: "
                + ", ".join(sorted(missing))
            )
        self.constitution = constitution
        self.runtime_policy = RuntimePolicy()
        self.project_root = project_root.resolve()
        self.poll_seconds = self._bounded_seconds(
            poll_seconds, default=2.0, minimum=1.0, maximum=3600.0
        )
        self.idle_seconds = self._bounded_seconds(
            idle_seconds, default=90.0, minimum=30.0, maximum=24.0 * 60.0 * 60.0
        )
        self._stop_event = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._known_stats: dict[str, tuple[int, int]] = {}
        self._last_source_change = time.monotonic()
        self._last_deep_analysis = 0.0
        self._analysis_queue = BackgroundAnalysisQueue(idle_seconds=self.idle_seconds)
        SAE_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_approval_policy()
        self._write_state("initialized", "SAE hazır; Constitution bağlı ve otomatik tarama henüz başlamadı.")

    # ---------- lifecycle ----------
    def start_automatic(self) -> None:
        """Açılış taramasını yapar ve canlı izleyiciyi başlatır."""
        self.runtime_policy.require("sae.boot_scan")
        self.runtime_policy.require("sae.live_watch")
        with self._lock:
            if self._watch_thread and self._watch_thread.is_alive():
                return
            self._write_state("boot_scan", "Açılış hızlı taraması çalışıyor.")
            self.scan(reason="boot")
            self._known_stats = self._snapshot_stats()
            self._stop_event.clear()
            self._watch_thread = threading.Thread(
                target=self._watch_loop,
                name="Jarvis-SAE-Watcher",
                daemon=True,
            )
            self._analysis_queue.start()
            self._watch_thread.start()
            self._write_state("watching", "Kaynak dosyaları canlı izleniyor.")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._watch_thread
        wait_seconds = self._bounded_seconds(
            timeout, default=3.0, minimum=0.2, maximum=30.0
        )
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=wait_seconds)
        self._analysis_queue.stop()

        with self._lock:
            current = self._watch_thread
            still_running = bool(current and current.is_alive())
            if not still_running:
                self._watch_thread = None

        if still_running:
            self._write_state(
                "stopping",
                "SAE izleyicisinin güvenli biçimde durması bekleniyor.",
            )
        else:
            self._write_state("stopped", "SAE güvenli biçimde durduruldu.")

    def mark_user_activity(self) -> None:
        """Derin analizin kullanıcı aktifken başlamasını geciktirir."""
        self._last_source_change = time.monotonic()
        self._analysis_queue.mark_activity()

    @property
    def background_analysis_is_running(self) -> bool:
        return self._analysis_queue.is_running

    @staticmethod
    def _bounded_seconds(
        value: object, *, default: float, minimum: float, maximum: float
    ) -> float:
        try:
            seconds = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        if not math.isfinite(seconds):
            return default
        return min(maximum, max(minimum, seconds))

    # ---------- scanning ----------
    def scan(self, reason: str = "manual") -> dict[str, Any]:
        operation = "sae.boot_scan" if reason == "boot" else "sae.manual_scan"
        self.runtime_policy.require(operation)
        with self._lock:
            previous = self.load_index()
            files: list[FileRecord] = []
            for path in self._iter_python_files():
                files.append(self._inspect_python(path))
            payload = self._make_payload(files, previous, reason)
            self._save(payload)
            self._known_stats = self._snapshot_stats()
            return payload

    def scan_changed(self, changed: set[str], removed: set[str]) -> dict[str, Any]:
        """Yalnızca değişen dosyaları günceller; tam tarama yapmaz."""
        self.runtime_policy.require("sae.incremental_scan")
        with self._lock:
            previous = self.load_index()
            records = {item.get("path", ""): item for item in previous.get("files", [])}
            for rel in removed:
                records.pop(rel, None)
            for rel in changed:
                path = self.project_root / rel
                if path.exists() and path.suffix.lower() == ".py" and not self._is_ignored(path):
                    records[rel] = self._file_to_dict(self._inspect_python(path))
            files = [records[key] for key in sorted(records)]
            payload: dict[str, Any] = {
                "schema_version": self.VERSION,
                "generated_at": self._now(),
                "project_root": str(self.project_root),
                "scan_reason": "live_change",
                "files": files,
            }
            payload["summary"] = self._summary(files)
            payload["changes"] = self._compare(previous, payload)
            self._save(payload)
            self._known_stats = self._snapshot_stats()
            return payload

    def _watch_loop(self) -> None:
        try:
            while not self._stop_event.wait(self.poll_seconds):
                try:
                    current = self._snapshot_stats()
                    old_paths, new_paths = set(self._known_stats), set(current)
                    removed = old_paths - new_paths
                    changed = {
                        path for path in new_paths
                        if path not in self._known_stats or current[path] != self._known_stats[path]
                    }
                    if changed or removed:
                        self._write_state("incremental_scan", f"{len(changed)} değişen, {len(removed)} silinen dosya işleniyor.")
                        self.scan_changed(changed, removed)
                        self._last_source_change = time.monotonic()
                        self._write_state("watching", "Canlı indeks güncel.")
                        continue
                    if time.monotonic() - self._last_source_change >= self.idle_seconds:
                        if time.monotonic() - self._last_deep_analysis >= 1800.0:
                            queued = self._analysis_queue.submit("sae.deep_analysis", self._run_idle_deep_analysis)
                            if queued:
                                self._write_state("analysis_queued", "Derin analiz sistem boşken çalışmak üzere kuyruğa alındı.")
                except Exception as exc:
                    self._write_state("error", f"SAE izleme hatası: {exc}")
        finally:
            with self._lock:
                if self._watch_thread is threading.current_thread():
                    self._watch_thread = None
            if self._stop_event.is_set():
                self._write_state("stopped", "SAE güvenli biçimde durduruldu.")


    def _run_idle_deep_analysis(self) -> None:
        self.deep_analysis()
        self._last_deep_analysis = time.monotonic()
        self._write_state("watching", "Boşta derin analiz tamamlandı.")

    # ---------- reports ----------
    def report(self, refresh: bool = False) -> str:
        data = self.scan(reason="manual") if refresh or not SAE_INDEX_FILE.exists() else self.load_index()
        summary = data.get("summary", {})
        changes = data.get("changes", {})
        state = self.runtime_state()
        lines = [
            "SAE — KENDİ KAYNAK KODU ENVANTERİ",
            f"Durum: {state.get('status', 'bilinmiyor')}",
            f"Son tarama: {data.get('generated_at', 'bilinmiyor')}",
            f"Tarama nedeni: {data.get('scan_reason', 'bilinmiyor')}",
            f"Python dosyası: {summary.get('python_files', 0)}",
            f"Sınıf: {summary.get('classes', 0)}",
            f"Fonksiyon/metot: {summary.get('functions', 0)}",
            f"Import bağlantısı: {summary.get('imports', 0)}",
            f"Sözdizimi hatalı dosya: {summary.get('syntax_errors', 0)}",
            "",
            "SON TARAMADAKİ DEĞİŞİKLİKLER",
            f"Eklenen: {len(changes.get('added', []))}",
            f"Değişen: {len(changes.get('modified', []))}",
            f"Silinen: {len(changes.get('removed', []))}",
            "",
            "GÜVENLİK POLİTİKASI",
            "Analiz otomatik; kaynak koduna yazma yalnızca kullanıcı onaylı EditManager akışıyla yapılabilir.",
        ]
        errors = [f for f in data.get("files", []) if f.get("syntax_error")]
        if errors:
            lines.extend(["", "SÖZDİZİMİ HATALARI"])
            lines.extend(f"- {item['path']}: {item['syntax_error']}" for item in errors[:20])
        return "\n".join(lines)

    def deep_analysis(self) -> dict[str, Any]:
        """Salt-okunur, düşük riskli ilk derin analiz raporu."""
        self.runtime_policy.require("sae.deep_analysis")
        with self._lock:
            data = self.load_index() or self.scan(reason="deep_analysis_bootstrap")
            files = data.get("files", [])
            duplicate_names: dict[str, list[str]] = {}
            very_large: list[dict[str, Any]] = []
            syntax_errors: list[dict[str, str]] = []
            for item in files:
                if item.get("size", 0) >= 150_000:
                    very_large.append({"path": item.get("path", ""), "size": item.get("size", 0)})
                if item.get("syntax_error"):
                    syntax_errors.append({"path": item.get("path", ""), "error": item.get("syntax_error", "")})
                for symbol in item.get("symbols", []):
                    if symbol.get("kind") in {"function", "method"}:
                        duplicate_names.setdefault(symbol.get("name", ""), []).append(item.get("path", ""))
            repeated = {
                name: sorted(set(paths)) for name, paths in duplicate_names.items()
                if name and len(set(paths)) >= 3 and not name.startswith("__")
            }
            report = {
                "generated_at": self._now(),
                "read_only": True,
                "approval_required_for_changes": True,
                "summary": {
                    "large_source_files": len(very_large),
                    "syntax_errors": len(syntax_errors),
                    "repeated_symbol_names": len(repeated),
                },
                "large_source_files": sorted(very_large, key=lambda x: x["size"], reverse=True)[:30],
                "syntax_errors": syntax_errors[:30],
                "repeated_symbol_names": dict(sorted(repeated.items())[:100]),
                "note": "Bunlar otomatik değişiklik talimatı değil, inceleme adaylarıdır.",
            }
            self._atomic_json_write(SAE_DEEP_REPORT_FILE, report)
            return report

    def deep_report(self, refresh: bool = False) -> str:
        if refresh or not SAE_DEEP_REPORT_FILE.exists():
            data = self.deep_analysis()
        else:
            try:
                data = read_json_object(
                    SAE_DEEP_REPORT_FILE, max_bytes=SAE_DEEP_REPORT_MAX_BYTES
                )
            except Exception:
                data = self.deep_analysis()
        summary = data.get("summary", {})
        return "\n".join([
            "SAE — DERİN ANALİZ (SALT OKUNUR)",
            f"Oluşturulma: {data.get('generated_at', 'bilinmiyor')}",
            f"Büyük kaynak dosyası adayı: {summary.get('large_source_files', 0)}",
            f"Sözdizimi hatası: {summary.get('syntax_errors', 0)}",
            f"Birden çok dosyada tekrarlanan sembol adı: {summary.get('repeated_symbol_names', 0)}",
            "Hiçbir kaynak dosyası değiştirilmedi. Düzeltme için açık kullanıcı onayı gerekir.",
        ])

    def find_symbol(self, query: str) -> str:
        data = self.load_index() or self.scan(reason="symbol_lookup")
        needle = query.casefold().strip()
        matches: list[tuple[str, dict[str, Any]]] = []
        for file_item in data.get("files", []):
            for symbol in file_item.get("symbols", []):
                full_name = f"{symbol.get('parent')}.{symbol.get('name')}".strip(".")
                if needle in full_name.casefold() or needle in symbol.get("name", "").casefold():
                    matches.append((file_item.get("path", ""), symbol))
        if not matches:
            return f"SAE indeksinde '{query}' adlı sınıf, fonksiyon veya metot bulunamadı."
        lines = [f"SAE SEMBOL ARAMASI: {query}"]
        for path, symbol in matches[:30]:
            parent = f" ({symbol['parent']})" if symbol.get("parent") else ""
            lines.append(f"- {symbol['kind']}: {symbol['name']}{parent} — {path}:{symbol['line']}")
        return "\n".join(lines)

    def runtime_state(self) -> dict[str, Any]:
        try:
            return read_json_object(SAE_STATE_FILE, max_bytes=SAE_STATE_MAX_BYTES)
        except Exception:
            return {}

    def load_index(self) -> dict[str, Any]:
        try:
            return read_json_object(SAE_INDEX_FILE, max_bytes=SAE_INDEX_MAX_BYTES)
        except Exception:
            return {}

    # ---------- internals ----------
    def _iter_python_files(self):
        for path in sorted(self.project_root.rglob("*.py")):
            if path.is_file() and not self._is_ignored(path):
                yield path

    def _is_ignored(self, path: Path) -> bool:
        try:
            relative_parts = path.relative_to(self.project_root).parts
        except ValueError:
            return True
        return any(part in IGNORED_DIRS for part in relative_parts)

    def _snapshot_stats(self) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        for path in self._iter_python_files():
            try:
                stat = path.stat()
                result[path.relative_to(self.project_root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
        return result

    def _inspect_python(self, path: Path) -> FileRecord:
        raw = path.read_bytes()
        stat = path.stat()
        text = raw.decode("utf-8", errors="replace")
        record = FileRecord(
            path=path.relative_to(self.project_root).as_posix(),
            size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            modified_ns=stat.st_mtime_ns,
        )
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            record.syntax_error = f"satır {exc.lineno}: {exc.msg}"
            return record
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(("." * node.level) + (node.module or ""))
        record.imports = sorted(imports)
        record.symbols = self._symbols(tree)
        return record

    def _make_payload(self, files: list[FileRecord], previous: dict[str, Any], reason: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.VERSION,
            "generated_at": self._now(),
            "project_root": str(self.project_root),
            "scan_reason": reason,
            "files": [self._file_to_dict(item) for item in files],
        }
        payload["summary"] = self._summary(payload["files"])
        payload["changes"] = self._compare(previous, payload)
        return payload

    @staticmethod
    def _symbols(tree: ast.AST) -> list[SymbolRecord]:
        records: list[SymbolRecord] = []
        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.parents: list[str] = []
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                records.append(SymbolRecord(node.name, "class", node.lineno, getattr(node, "end_lineno", node.lineno), ".".join(self.parents)))
                self.parents.append(node.name); self.generic_visit(node); self.parents.pop()
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                records.append(SymbolRecord(node.name, "method" if self.parents else "function", node.lineno, getattr(node, "end_lineno", node.lineno), ".".join(self.parents)))
                self.parents.append(node.name); self.generic_visit(node); self.parents.pop()
            visit_AsyncFunctionDef = visit_FunctionDef
        Visitor().visit(tree)
        return records

    @staticmethod
    def _file_to_dict(item: FileRecord) -> dict[str, Any]:
        return asdict(item)

    @staticmethod
    def _summary(files: list[dict[str, Any]]) -> dict[str, int]:
        symbols = [symbol for item in files for symbol in item.get("symbols", [])]
        return {
            "python_files": len(files),
            "classes": sum(symbol.get("kind") == "class" for symbol in symbols),
            "functions": sum(symbol.get("kind") in {"function", "method"} for symbol in symbols),
            "imports": sum(len(item.get("imports", [])) for item in files),
            "syntax_errors": sum(bool(item.get("syntax_error")) for item in files),
        }

    @staticmethod
    def _compare(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, list[str]]:
        old = {item.get("path", ""): item.get("sha256", "") for item in previous.get("files", [])}
        new = {item.get("path", ""): item.get("sha256", "") for item in current.get("files", [])}
        return {
            "added": sorted(set(new) - set(old)),
            "removed": sorted(set(old) - set(new)),
            "modified": sorted(path for path in set(old) & set(new) if old[path] != new[path]),
        }

    def _save(self, payload: dict[str, Any]) -> None:
        self._atomic_json_write(SAE_INDEX_FILE, payload)
        history_item = {
            "generated_at": payload.get("generated_at"),
            "scan_reason": payload.get("scan_reason"),
            "summary": payload.get("summary", {}),
            "changes": payload.get("changes", {}),
        }
        encoded = (json.dumps(history_item, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        SAE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with SAE_HISTORY_FILE.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                start = handle.tell()
                try:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                except BaseException:
                    handle.seek(start)
                    handle.truncate()
                    handle.flush()
                    raise

    def _write_state(self, status: str, message: str) -> None:
        self._atomic_json_write(SAE_STATE_FILE, {
            "updated_at": self._now(), "status": status, "message": message,
            "watcher_alive": bool(self._watch_thread and self._watch_thread.is_alive()),
            "source_write_access": False,
            "approval_required_for_changes": True,
        })

    @staticmethod
    def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
        atomic_write_json(path, payload)

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _ensure_approval_policy() -> None:
        policy = {
            "schema_version": 1,
            "automatic_analysis": True,
            "automatic_indexing": True,
            "automatic_source_changes": False,
            "source_changes_require_explicit_user_approval": True,
            "change_executor": "EditManager pending proposal approval flow",
        }
        if not SAE_APPROVAL_POLICY_FILE.exists():
            SelfAwarenessEngine._atomic_json_write(SAE_APPROVAL_POLICY_FILE, policy)
