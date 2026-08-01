from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from artmach_assistant.core.store_validation import read_json_object
from artmach_assistant.core.workspace import WorkspaceError

_MAX_METADATA_BYTES = 128 * 1024
_MAX_LOG_BYTES = 256 * 1024
_PACKAGE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SUPPORTED_TEMPLATES = {"python_desktop", "python_cli", "python_library"}


@dataclass(frozen=True, slots=True)
class ProjectLaunchSpec:
    root: str
    project_name: str
    package_name: str
    template: str
    command: tuple[str, ...]
    description: str

    def display_command(self) -> str:
        return subprocess.list2cmdline(list(self.command))


@dataclass(frozen=True, slots=True)
class ProjectLaunchResult:
    root: str
    project_name: str
    pid: int
    status: str
    command: tuple[str, ...]
    output: str = ""

    @property
    def running(self) -> bool:
        return self.status == "running"

    def report(self) -> str:
        state = "ÇALIŞIYOR" if self.running else "TAMAMLANDI"
        lines = [
            f"PROJE ÇALIŞTIRMA: {state}",
            f"Proje: {self.project_name}",
            f"PID: {self.pid if self.pid > 0 else '-'}",
            f"Komut: {subprocess.list2cmdline(list(self.command))}",
        ]
        if self.output.strip():
            lines.extend(("", self.output.strip()[-12000:]))
        return "\n".join(lines)


@dataclass(slots=True)
class _TrackedProcess:
    process: subprocess.Popen[str]
    log_handle: IO[str]
    log_path: Path
    spec: ProjectLaunchSpec


class ProjectLaunchService:
    """Launch only Jarvis-created projects using validated local metadata."""

    def __init__(self, python_executable: str | None = None) -> None:
        self.python_executable = str(python_executable or sys.executable)
        self._lock = threading.RLock()
        self._processes: dict[str, _TrackedProcess] = {}

    @staticmethod
    def _root(root: str | Path) -> Path:
        value = str(root or "").strip()
        if not value:
            raise WorkspaceError("Çalıştırılacak proje klasörü seçilmedi.")
        candidate = Path(value).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WorkspaceError(f"Proje klasörü bulunamadı: {candidate}") from exc
        if not resolved.is_dir() or resolved.is_symlink():
            raise WorkspaceError("Proje kökü gerçek ve sembolik bağlantı olmayan bir klasör olmalıdır.")
        return resolved

    @staticmethod
    def _metadata(root: Path) -> dict[str, object]:
        path = root / ".jarvis" / "project.json"
        if not path.is_file() or path.is_symlink():
            raise WorkspaceError(
                "Bu proje Jarvis başlangıç metadatası içermiyor. Güvenlik nedeniyle "
                "yalnızca Jarvis tarafından oluşturulan projeler doğrudan çalıştırılabilir."
            )
        try:
            payload = read_json_object(path, max_bytes=_MAX_METADATA_BYTES)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise WorkspaceError(f"Proje çalıştırma metadatası okunamadı: {exc}") from exc
        if payload.get("schema_version") != 1:
            raise WorkspaceError("Desteklenmeyen proje çalıştırma metadata sürümü.")
        return payload

    def plan(self, root: str | Path) -> ProjectLaunchSpec:
        resolved = self._root(root)
        payload = self._metadata(resolved)
        project_name = " ".join(str(payload.get("project_name", resolved.name)).split())[:300]
        package_name = str(payload.get("package_name", "")).strip()
        template = str(payload.get("template", "")).strip().casefold()
        if not _PACKAGE_NAME.fullmatch(package_name):
            raise WorkspaceError("Proje paket adı güvenli Python modül biçiminde değil.")
        if template not in _SUPPORTED_TEMPLATES:
            raise WorkspaceError(f"Desteklenmeyen proje başlangıç türü: {template or '(boş)'}")
        source_root = resolved / "src"
        package_root = source_root / package_name
        if not source_root.is_dir() or source_root.is_symlink():
            raise WorkspaceError("Projenin src klasörü bulunamadı veya güvenli değil.")
        if not package_root.is_dir() or package_root.is_symlink():
            raise WorkspaceError("Projenin Python paket klasörü bulunamadı veya güvenli değil.")
        if template in {"python_desktop", "python_cli"}:
            entrypoint = package_root / "__main__.py"
            if not entrypoint.is_file() or entrypoint.is_symlink():
                raise WorkspaceError("Proje __main__.py giriş noktası bulunamadı.")
            command = (self.python_executable, "-m", package_name)
            description = (
                "PySide6 masaüstü uygulamasını başlatır."
                if template == "python_desktop"
                else "Komut satırı uygulamasını başlatır."
            )
        else:
            api_path = package_root / "api.py"
            if not api_path.is_file() or api_path.is_symlink():
                raise WorkspaceError("Kütüphane api.py başlangıç dosyası bulunamadı.")
            statement = f"from {package_name}.api import describe; print(describe())"
            command = (self.python_executable, "-c", statement)
            description = "Kütüphanenin güvenli describe() başlangıç kontrolünü çalıştırır."
        return ProjectLaunchSpec(
            root=str(resolved),
            project_name=project_name or resolved.name,
            package_name=package_name,
            template=template,
            command=command,
            description=description,
        )

    @staticmethod
    def _environment(spec: ProjectLaunchSpec) -> dict[str, str]:
        environment = os.environ.copy()
        source_root = str(Path(spec.root) / "src")
        previous = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = source_root + (os.pathsep + previous if previous else "")
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        return environment

    @staticmethod
    def _log_path(root: Path) -> Path:
        directory = root / ".jarvis"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "last_run.log"
        if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
            try:
                path.replace(directory / "last_run.previous.log")
            except OSError:
                pass
        return path

    def _cleanup_finished(self, key: str) -> ProjectLaunchResult | None:
        tracked = self._processes.get(key)
        if tracked is None:
            return None
        code = tracked.process.poll()
        if code is None:
            return ProjectLaunchResult(
                root=tracked.spec.root,
                project_name=tracked.spec.project_name,
                pid=int(tracked.process.pid or 0),
                status="running",
                command=tracked.spec.command,
            )
        try:
            tracked.log_handle.flush()
            tracked.log_handle.close()
        except OSError:
            pass
        output = ""
        try:
            output = tracked.log_path.read_text(encoding="utf-8", errors="replace")[-_MAX_LOG_BYTES:]
        except OSError:
            pass
        self._processes.pop(key, None)
        return ProjectLaunchResult(
            root=tracked.spec.root,
            project_name=tracked.spec.project_name,
            pid=int(tracked.process.pid or 0),
            status="completed" if code == 0 else "failed",
            command=tracked.spec.command,
            output=output,
        )

    def launch(self, root: str | Path) -> ProjectLaunchResult:
        spec = self.plan(root)
        key = str(Path(spec.root)).casefold()
        with self._lock:
            existing = self._cleanup_finished(key)
            if existing is not None and existing.running:
                raise WorkspaceError(
                    f"{spec.project_name} zaten çalışıyor. PID: {existing.pid}."
                )
            root_path = Path(spec.root)
            log_path = self._log_path(root_path)
            log_handle = log_path.open("w", encoding="utf-8", newline="\n")
            creationflags = 0
            if os.name == "nt":
                creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            try:
                process = subprocess.Popen(
                    list(spec.command),
                    cwd=spec.root,
                    env=self._environment(spec),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    text=True,
                    creationflags=creationflags,
                )
            except Exception:
                log_handle.close()
                raise
            tracked = _TrackedProcess(process, log_handle, log_path, spec)
            self._processes[key] = tracked
        # Detect immediate startup failures without blocking a desktop app.
        time.sleep(0.15)
        with self._lock:
            result = self._cleanup_finished(key)
            if result is not None and result.status == "failed":
                raise WorkspaceError(
                    "Proje başlangıçta hata verdi.\n" + (result.output[-12000:] or "Çıktı yok.")
                )
            if result is not None and result.status == "completed":
                return result
            return ProjectLaunchResult(
                root=spec.root,
                project_name=spec.project_name,
                pid=int(process.pid or 0),
                status="running",
                command=spec.command,
            )

    def status(self, root: str | Path) -> ProjectLaunchResult | None:
        resolved = self._root(root)
        key = str(resolved).casefold()
        with self._lock:
            return self._cleanup_finished(key)

    def stop(self, root: str | Path) -> ProjectLaunchResult:
        resolved = self._root(root)
        key = str(resolved).casefold()
        with self._lock:
            tracked = self._processes.get(key)
            if tracked is None:
                raise WorkspaceError("Bu proje için Jarvis tarafından başlatılmış etkin süreç yok.")
            if tracked.process.poll() is None:
                tracked.process.terminate()
                try:
                    tracked.process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    tracked.process.kill()
                    tracked.process.wait(timeout=3.0)
            result = self._cleanup_finished(key)
        assert result is not None
        return ProjectLaunchResult(
            root=result.root,
            project_name=result.project_name,
            pid=result.pid,
            status="stopped",
            command=result.command,
            output=result.output,
        )

    def close(self) -> None:
        with self._lock:
            roots = [tracked.spec.root for tracked in self._processes.values()]
        for root in roots:
            try:
                self.stop(root)
            except Exception:
                pass
