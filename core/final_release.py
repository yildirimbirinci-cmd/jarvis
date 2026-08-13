from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import tokenize
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable


RELEASE_SCHEMA_VERSION = 1
REQUIRED_PROJECT_ENTRIES = (
    "app.py",
    "__main__.py",
    "config.py",
    "core",
    "indexing",
    "tests",
    "tools",
)
EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".vs",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak", ".part"}
EXCLUDED_ROOT_NAMES = {
    "FILES.txt",
    "SHA256SUMS.txt",
    "DELIVERY_NOTES_TR.md",
    "README_TR.md",
}


class FinalReleaseError(RuntimeError):
    """Raised when a final release cannot be built or installed safely."""


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    size: int
    sha256: str


@dataclass
class ReleaseManifest:
    release_id: str
    created_at: str
    version: str
    acceptance_run_id: str
    acceptance_profile: str
    acceptance_ready: bool
    python: str
    platform: str
    architecture_bits: int
    files: list[ReleaseFile] = field(default_factory=list)
    schema_version: int = RELEASE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["files"] = [asdict(item) for item in self.files]
        return payload


@dataclass(frozen=True)
class FirstRunCheck:
    name: str
    state: str
    required: bool
    detail: str


@dataclass
class FirstRunReport:
    ready: bool
    created_at: str
    project_root: str
    checks: list[FirstRunCheck]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "ready": self.ready,
            "created_at": self.created_at,
            "project_root": self.project_root,
            "checks": [asdict(item) for item in self.checks],
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )


def load_json_object(path: Path, *, max_bytes: int = 4 * 1024 * 1024) -> dict[str, object]:
    if not path.is_file():
        raise FinalReleaseError(f"JSON dosyası bulunamadı: {path}")
    if path.stat().st_size > max_bytes:
        raise FinalReleaseError(f"JSON dosyası çok büyük: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FinalReleaseError(f"JSON kökü nesne olmalı: {path}")
    return payload


def validate_acceptance_report(report_path: Path) -> dict[str, object]:
    payload = load_json_object(report_path)
    if bool(payload.get("cancelled", False)):
        raise FinalReleaseError("Son kabul testi iptal edilmiş; final teslim oluşturulamaz.")
    if str(payload.get("profile", "")).casefold() != "full":
        raise FinalReleaseError("Son kabul profili 'full' değil.")
    if not bool(payload.get("ready", False)):
        raise FinalReleaseError("Son kabul raporu hazır durumunda değil.")
    checks = payload.get("checks", [])
    if not isinstance(checks, list) or not checks:
        raise FinalReleaseError("Kabul raporunda kontrol listesi yok.")
    failed = [
        item
        for item in checks
        if isinstance(item, dict)
        and bool(item.get("required", True))
        and str(item.get("state", "")).casefold() not in {"passed", "manual"}
    ]
    if failed:
        raise FinalReleaseError("Kabul raporunda başarısız zorunlu kontroller var.")
    return payload


def validate_project_root(project_root: Path) -> None:
    root = project_root.expanduser().resolve()
    missing = [name for name in REQUIRED_PROJECT_ENTRIES if not (root / name).exists()]
    if missing:
        raise FinalReleaseError(
            "Proje kökü eksik: " + ", ".join(sorted(missing))
        )
    if root.is_symlink():
        raise FinalReleaseError("Proje kökü sembolik bağlantı olamaz.")


def _is_excluded(relative: Path) -> bool:
    parts = relative.parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return True
    name = relative.name
    if name in EXCLUDED_DIR_NAMES:
        return True
    if relative.parent == Path(".") and name in EXCLUDED_ROOT_NAMES:
        return True
    if name.startswith(".") and name.endswith(".tmp"):
        return True
    if relative.suffix.casefold() in EXCLUDED_FILE_SUFFIXES:
        return True
    if name.casefold().endswith((".zip", ".7z", ".rar")):
        return True
    return False


def iter_release_files(project_root: Path) -> Iterable[tuple[Path, Path]]:
    root = project_root.expanduser().resolve()
    validate_project_root(root)
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(root)
        except ValueError as exc:
            raise FinalReleaseError(f"Proje kökü dışındaki dosya reddedildi: {path}") from exc
        if _is_excluded(relative):
            continue
        yield path, relative


def validate_python_sources(project_root: Path) -> int:
    count = 0
    for source, relative in iter_release_files(project_root):
        if relative.suffix.casefold() != ".py":
            continue
        try:
            with tokenize.open(source) as handle:
                content = handle.read()
            compile(content, str(relative), "exec")
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise FinalReleaseError(f"Python kaynak doğrulaması başarısız: {relative}: {exc}") from exc
        count += 1
    return count


def copy_clean_project(project_root: Path, destination: Path) -> list[ReleaseFile]:
    root = project_root.expanduser().resolve()
    target = destination.expanduser().resolve()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=False)
    for directory in sorted(root.rglob("*")):
        if directory.is_symlink() or not directory.is_dir():
            continue
        relative_dir = directory.relative_to(root)
        if _is_excluded(relative_dir):
            continue
        (target / relative_dir).mkdir(parents=True, exist_ok=True)
    files: list[ReleaseFile] = []
    for source, relative in iter_release_files(root):
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
        files.append(
            ReleaseFile(
                path=(Path("artmach_assistant") / relative).as_posix(),
                size=output.stat().st_size,
                sha256=sha256_file(output),
            )
        )
    return files


def _release_id(acceptance_run_id: str) -> str:
    seed = f"{acceptance_run_id}|{utc_now()}|{os.getpid()}".encode("utf-8")
    return "REL-" + hashlib.sha256(seed).hexdigest()[:12].upper()


def _launcher_body(command_with_python: str) -> str:
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "if exist \"%~dp0.venv\\Scripts\\python.exe\" (\r\n"
        f"  \"%~dp0.venv\\Scripts\\python.exe\" {command_with_python}\r\n"
        "  exit /b %errorlevel%\r\n"
        ")\r\n"
        "where py >nul 2>&1\r\n"
        "if not errorlevel 1 (\r\n"
        f"  py -3.11 {command_with_python}\r\n"
        "  exit /b %errorlevel%\r\n"
        ")\r\n"
        "where python >nul 2>&1\r\n"
        "if not errorlevel 1 (\r\n"
        f"  python {command_with_python}\r\n"
        "  exit /b %errorlevel%\r\n"
        ")\r\n"
        "echo Python 3.11 bulunamadi.\r\n"
        "pause\r\n"
        "exit /b 1\r\n"
    )


def launcher_files() -> dict[str, str]:
    return {
        "Jarvis_Baslat.cmd": _launcher_body("-m artmach_assistant"),
        "Jarvis_Ilk_Kontrol.cmd": _launcher_body(
            "artmach_assistant\\tools\\first_run_check.py --project-root artmach_assistant"
        ) + "pause\r\n",
        "Jarvis_Kur_veya_Guncelle.cmd": _launcher_body(
            "artmach_assistant\\tools\\install_final_release.py --release-root . --destination ."
        ) + "pause\r\n",
    }


def _write_sha256sums(root: Path, files: list[ReleaseFile]) -> None:
    rows = [f"{item.sha256}  {item.path}" for item in sorted(files, key=lambda item: item.path)]
    for name in sorted(launcher_files()):
        path = root / name
        rows.append(f"{sha256_file(path)}  {name}")
    rows.append(f"{sha256_file(root / 'RELEASE.json')}  RELEASE.json")
    rows.append(f"{sha256_file(root / 'README_TR.md')}  README_TR.md")
    atomic_write_text(root / "SHA256SUMS.txt", "\n".join(rows) + "\n")


def _zip_tree(source_root: Path, target_zip: Path) -> None:
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_zip.with_name(f".{target_zip.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source_root.rglob("*")):
                if path.is_symlink():
                    continue
                relative = path.relative_to(source_root).as_posix()
                if path.is_dir():
                    archive.writestr(relative.rstrip("/") + "/", b"")
                elif path.is_file():
                    archive.write(path, relative)
        os.replace(temporary, target_zip)
    finally:
        if temporary.exists():
            temporary.unlink()


def _render_readme(manifest: ReleaseManifest) -> str:
    return f"""# Artmach Assistant / Jarvis — Nihai Kaynak Teslimi

Bu teslim, Windows tam uçtan uca kabul testinde hazır durumu alan yerel kaynak ağacından üretildi.

- Release kimliği: `{manifest.release_id}`
- Sürüm: `{manifest.version}`
- Kabul çalışması: `{manifest.acceptance_run_id}`
- Kabul profili: `{manifest.acceptance_profile}`
- Python: `{manifest.python}`
- Dosya sayısı: `{len(manifest.files)}`

## İlk kullanım

1. `Jarvis_Ilk_Kontrol.cmd` dosyasını çalıştır.
2. Zorunlu kontroller başarılıysa `Jarvis_Baslat.cmd` ile Jarvis'i aç.
3. Güncelleme veya başka klasöre kurulum için `Jarvis_Kur_veya_Guncelle.cmd` kullan.

Kurulum aracı mevcut hedefi değiştirmeden önce otomatik geri dönüş ZIP'i oluşturur. `.venv`, kullanıcı ayarları ve `%LOCALAPPDATA%\\ArtmachAssistant` verileri kaynak paketine dahil edilmez.
"""


def build_final_release(
    project_root: str | Path,
    acceptance_report: str | Path,
    output_dir: str | Path,
    *,
    version: str = "1.0.0",
) -> dict[str, Path | str | int]:
    root = Path(project_root).expanduser().resolve()
    report_path = Path(acceptance_report).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    validate_project_root(root)
    validate_python_sources(root)
    acceptance = validate_acceptance_report(report_path)
    run_id = str(acceptance.get("run_id") or acceptance.get("id") or "unknown")
    release_id = _release_id(run_id)
    destination.mkdir(parents=True, exist_ok=True)

    working = Path(tempfile.mkdtemp(prefix="jarvis-final-release-", dir=destination))
    release_root = working / f"Artmach_Assistant_{version}"
    package_target = release_root / "artmach_assistant"
    try:
        files = copy_clean_project(root, package_target)
        manifest = ReleaseManifest(
            release_id=release_id,
            created_at=utc_now(),
            version=version,
            acceptance_run_id=run_id,
            acceptance_profile=str(acceptance.get("profile", "full")),
            acceptance_ready=True,
            python=str(acceptance.get("python") or sys.version.split()[0]),
            platform=str(acceptance.get("platform") or sys.platform),
            architecture_bits=struct.calcsize("P") * 8,
            files=files,
        )
        atomic_write_json(release_root / "RELEASE.json", manifest.to_dict())
        atomic_write_text(release_root / "README_TR.md", _render_readme(manifest))
        for name, content in launcher_files().items():
            atomic_write_text(release_root / name, content)
        _write_sha256sums(release_root, files)

        source_zip = destination / f"Artmach_Assistant_Final_Source_{release_id}.zip"
        _zip_tree(release_root, source_zip)
        rollback_zip = destination / f"Artmach_Assistant_Rollback_{release_id}.zip"
        _zip_tree(package_target, rollback_zip)

        manifest_copy = destination / f"Artmach_Assistant_Final_Manifest_{release_id}.json"
        atomic_write_json(manifest_copy, manifest.to_dict())
        return {
            "release_id": release_id,
            "source_zip": source_zip,
            "rollback_zip": rollback_zip,
            "manifest": manifest_copy,
            "file_count": len(files),
        }
    finally:
        shutil.rmtree(working, ignore_errors=True)


def _safe_archive_name(name: str) -> PurePosixPath:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise FinalReleaseError(f"Güvensiz arşiv yolu: {name}")
    return pure


def verify_release_tree(release_root: Path) -> None:
    sums_path = release_root / "SHA256SUMS.txt"
    if not sums_path.is_file():
        raise FinalReleaseError("SHA256SUMS.txt bulunamadı.")
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, separator, relative = line.partition("  ")
        if not separator or len(digest) != 64:
            raise FinalReleaseError("SHA256SUMS.txt biçimi geçersiz.")
        pure = _safe_archive_name(relative)
        path = release_root.joinpath(*pure.parts)
        if not path.is_file():
            raise FinalReleaseError(f"Release dosyası eksik: {relative}")
        if sha256_file(path) != digest:
            raise FinalReleaseError(f"Release dosyası hash uyuşmazlığı: {relative}")


def install_release(
    release_root: str | Path,
    destination: str | Path,
    *,
    backup_dir: str | Path | None = None,
    compile_python: str | Path | None = None,
    data_root: str | Path | None = None,
) -> dict[str, str]:
    source = Path(release_root).expanduser().resolve()
    target_parent = Path(destination).expanduser().resolve()
    verify_release_tree(source)
    source_package = source / "artmach_assistant"
    validate_project_root(source_package)
    target_parent.mkdir(parents=True, exist_ok=True)
    target = target_parent / "artmach_assistant"
    backups = (
        Path(backup_dir).expanduser().resolve()
        if backup_dir is not None
        else target_parent / "rollback"
    )
    backups.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_zip = backups / f"artmach_assistant_before_{timestamp}.zip"
    if target.exists():
        _zip_tree(target, backup_zip)

    staging = target_parent / f".artmach_assistant.installing.{os.getpid()}"
    old = target_parent / f".artmach_assistant.previous.{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    try:
        shutil.copytree(source_package, staging, symlinks=False)
        if compile_python is not None:
            python_path = Path(compile_python).expanduser().resolve()
            if not python_path.is_file():
                raise FinalReleaseError(f"Kurulum oncesi derleme araci bulunamadi: {python_path}")
            python = str(python_path)
            try:
                result = subprocess.run(
                    [python, "-m", "compileall", "-q", str(staging)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise FinalReleaseError(f"Kurulum oncesi derleme baslatilamadi: {exc}") from exc
            if result.returncode != 0:
                raise FinalReleaseError("Kurulum oncesi derleme basarisiz: " + result.stdout[-2000:])
        if target.exists():
            os.replace(target, old)
        os.replace(staging, target)
        for name in launcher_files():
            source_file = source / name
            target_file = target_parent / name
            if source_file.resolve() != target_file.resolve():
                shutil.copy2(source_file, target_file)
        release_source = source / "RELEASE.json"
        release_target = target_parent / "RELEASE.json"
        if release_source.resolve() != release_target.resolve():
            shutil.copy2(release_source, release_target)
        if old.exists():
            shutil.rmtree(old)
        try:
            from artmach_assistant.core.deployment_layout import DeploymentPaths
            deployment = DeploymentPaths.resolve(target, data_root=data_root)
            deployment.ensure_persistent_tree()
            persistent_data_root = str(deployment.data_root)
        except Exception:
            persistent_data_root = str(Path(data_root).expanduser().resolve()) if data_root is not None else ""
        record = {
            "schema_version": 2,
            "installed_at": utc_now(),
            "source": str(source),
            "destination": str(target),
            "application_root": str(target),
            "persistent_data_root": persistent_data_root,
            "backup": str(backup_zip) if backup_zip.exists() else "",
        }
        atomic_write_json(target_parent / "INSTALLATION.json", record)
        return {
            "destination": str(target),
            "backup": str(backup_zip) if backup_zip.exists() else "",
            "persistent_data_root": persistent_data_root,
        }
    except Exception:
        if target.exists() and old.exists():
            shutil.rmtree(target, ignore_errors=True)
        if old.exists() and not target.exists():
            os.replace(old, target)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if old.exists() and target.exists():
            shutil.rmtree(old, ignore_errors=True)



def restore_application_backup(
    backup_zip: str | Path,
    destination: str | Path,
) -> dict[str, str]:
    """Restore only application files from a verified local rollback archive.

    Persistent ECHO data is deliberately outside the application tree and is
    never touched by this operation.
    """
    backup = Path(backup_zip).expanduser().resolve()
    target_parent = Path(destination).expanduser().resolve()
    target = target_parent / "artmach_assistant"
    if not backup.is_file():
        raise FinalReleaseError(f"Rollback arsivi bulunamadi: {backup}")
    staging = target_parent / f".artmach_assistant.rollback.{os.getpid()}"
    old = target_parent / f".artmach_assistant.rollback-old.{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    try:
        staging.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(backup) as archive:
            for name in archive.namelist():
                _safe_archive_name(name)
            archive.extractall(staging)
        validate_project_root(staging)
        if target.exists():
            os.replace(target, old)
        os.replace(staging, target)
        shutil.rmtree(old, ignore_errors=True)
        return {"destination": str(target), "backup": str(backup)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if old.exists() and not target.exists():
            os.replace(old, target)
        raise
    finally:
        if old.exists() and target.exists():
            shutil.rmtree(old, ignore_errors=True)


def uninstall_release(
    destination: str | Path,
    *,
    backup_dir: str | Path | None = None,
    data_root: str | Path | None = None,
    purge_persistent_data: bool = False,
) -> dict[str, str]:
    """Remove the installed application while preserving user data by default."""
    target_parent = Path(destination).expanduser().resolve()
    target = target_parent / "artmach_assistant"
    backups = (
        Path(backup_dir).expanduser().resolve()
        if backup_dir is not None
        else target_parent / "rollback"
    )
    backups.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_zip = backups / f"artmach_assistant_uninstall_{timestamp}.zip"
    if target.exists():
        _zip_tree(target, backup_zip)
        shutil.rmtree(target)

    for name in (*launcher_files().keys(), "RELEASE.json", "INSTALLATION.json"):
        path = target_parent / name
        if path.is_file():
            path.unlink()

    persistent_data_root = ""
    try:
        from artmach_assistant.core.deployment_layout import DeploymentPaths
        deployment = DeploymentPaths.resolve(target, data_root=data_root)
        persistent_data_root = str(deployment.data_root)
        if purge_persistent_data and deployment.data_root.exists():
            shutil.rmtree(deployment.data_root)
    except Exception:
        if data_root is not None:
            persistent = Path(data_root).expanduser().resolve()
            persistent_data_root = str(persistent)
            if purge_persistent_data and persistent.exists():
                shutil.rmtree(persistent)

    return {
        "destination": str(target),
        "backup": str(backup_zip) if backup_zip.exists() else "",
        "persistent_data_root": persistent_data_root,
        "persistent_data_preserved": "false" if purge_persistent_data else "true",
    }

def _check_import(name: str) -> tuple[bool, str]:
    try:
        module = __import__(name)
        version = getattr(module, "__version__", "hazır")
        return True, str(version)
    except Exception as exc:
        return False, str(exc)


def default_acceptance_path(data_root: Path | None = None) -> Path:
    if data_root is not None:
        return data_root / "logs" / "acceptance" / "e2e_latest.json"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ArtmachAssistant" / "logs" / "acceptance" / "e2e_latest.json"
    return Path.home() / ".local" / "share" / "ArtmachAssistant" / "logs" / "acceptance" / "e2e_latest.json"


def run_first_run_checks(
    project_root: str | Path,
    *,
    acceptance_report: str | Path | None = None,
    import_checker: Callable[[str], tuple[bool, str]] = _check_import,
) -> FirstRunReport:
    root = Path(project_root).expanduser().resolve()
    checks: list[FirstRunCheck] = []
    try:
        validate_project_root(root)
        checks.append(FirstRunCheck("Proje yapısı", "passed", True, "Zorunlu kaynaklar bulundu."))
    except Exception as exc:
        checks.append(FirstRunCheck("Proje yapısı", "failed", True, str(exc)))

    bits = struct.calcsize("P") * 8
    version_ok = sys.version_info[:2] == (3, 11)
    checks.append(
        FirstRunCheck(
            "Python",
            "passed" if version_ok and bits == 64 else "failed",
            True,
            f"Python {sys.version.split()[0]} | {bits}-bit",
        )
    )
    for module_name, label, required in (
        ("PySide6", "PySide6", True),
        ("pytest", "pytest", True),
    ):
        ok, detail = import_checker(module_name)
        checks.append(FirstRunCheck(label, "passed" if ok else "failed", required, detail))

    report_path = (
        Path(acceptance_report).expanduser().resolve()
        if acceptance_report is not None
        else default_acceptance_path()
    )
    try:
        acceptance = validate_acceptance_report(report_path)
        checks.append(
            FirstRunCheck(
                "Tam Windows kabulü",
                "passed",
                True,
                f"Hazır; çalışma={acceptance.get('run_id', 'bilinmiyor')}",
            )
        )
    except Exception as exc:
        checks.append(FirstRunCheck("Tam Windows kabulü", "failed", True, str(exc)))

    piper = root / "tools" / "piper" / "piper.exe"
    model_candidates = list((root / "models" / "piper").glob("*.onnx")) if (root / "models" / "piper").is_dir() else []
    checks.append(
        FirstRunCheck(
            "Piper",
            "passed" if piper.is_file() and bool(model_candidates) else "warning",
            False,
            f"çalıştırıcı={'var' if piper.is_file() else 'yok'}; model={len(model_candidates)}",
        )
    )
    ready = all(item.state == "passed" for item in checks if item.required)
    return FirstRunReport(ready=ready, created_at=utc_now(), project_root=str(root), checks=checks)


def save_first_run_report(report: FirstRunReport, target: Path) -> None:
    atomic_write_json(target, report.to_dict())
