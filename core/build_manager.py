from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from artmach_assistant.core.store_validation import read_json_object
from artmach_assistant.core.workspace import WorkspaceError, WorkspaceService

_MAX_COMMAND_PARTS = 128
_MAX_COMMAND_PART_CHARS = 8_192
_MAX_OUTPUT_CHARS = 50_000
_MAX_TIMEOUT_SECONDS = 86_400


def _safe_text(value: Any, *, limit: int) -> str:
    try:
        text = value if isinstance(value, str) else str(value)
    except BaseException:
        return "<metin dönüştürülemedi>"
    return text.replace("\x00", "")[:limit]


@dataclass(frozen=True)
class BuildProfile:
    name: str
    command: list[str]
    description: str

    def display_command(self) -> str:
        return subprocess.list2cmdline([_safe_text(part, limit=_MAX_COMMAND_PART_CHARS) for part in self.command])


@dataclass
class BuildResult:
    profile: BuildProfile
    return_code: int
    output: str

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0

    def report(self) -> str:
        state = "BAŞARILI" if self.succeeded else "BAŞARISIZ"
        return (
            f"SONUÇ: {state}\n"
            f"GÖREV: {_safe_text(self.profile.name, limit=500)}\n"
            f"KOMUT: {self.profile.display_command()}\n"
            f"ÇIKIŞ KODU: {self.return_code}\n\n"
            f"{_safe_text(self.output, limit=_MAX_OUTPUT_CHARS) or '(çıktı yok)'}"
        )


class BuildPipelineResult:
    def __init__(self, results: list[BuildResult]) -> None:
        self.results = [result for result in results if isinstance(result, BuildResult)]

    @property
    def succeeded(self) -> bool:
        return bool(self.results) and all(x.succeeded for x in self.results)

    def report(self) -> str:
        return "\n\n".join(x.report() for x in self.results)


@dataclass(frozen=True, slots=True)
class BuildProgressEvent:
    completed: int
    total: int
    profile_name: str
    phase: str
    elapsed_seconds: int = 0


class BuildManager:
    """Only runs detected, predefined project commands. No free-form shell execution."""

    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def detect_profiles(self) -> list[BuildProfile]:
        root = self.workspace.require_root()
        profiles: list[BuildProfile] = []
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "requirements.txt").exists():
            profiles.append(BuildProfile(
                "Python sözdizimi kontrolü",
                [self._python(), "-m", "compileall", "-q", "."],
                "Tüm Python dosyalarını bytecode derleyerek sözdizimi hatalarını bulur.",
            ))
            if (root / "tests").is_dir() or (root / "pytest.ini").exists():
                profiles.append(BuildProfile(
                    "Python testleri (pytest)",
                    [self._python(), "-m", "pytest", "-q"],
                    "Projede pytest testlerini çalıştırır.",
                ))
        package_json = root / "package.json"
        if package_json.is_file() and not package_json.is_symlink():
            npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
            scripts = self._package_scripts(package_json)
            if "test" in scripts:
                profiles.append(BuildProfile("NPM test", [npm, "test", "--", "--runInBand"], "package.json test scriptini çalıştırır."))
            if "build" in scripts:
                profiles.append(BuildProfile("NPM build", [npm, "run", "build"], "package.json build scriptini çalıştırır."))
        if (root / "CMakeLists.txt").is_file():
            cmake = shutil.which("cmake") or "cmake"
            profiles.extend([
                BuildProfile("CMake yapılandır", [cmake, "-S", ".", "-B", "build"], "Projeyi build klasörüne yapılandırır."),
                BuildProfile("CMake build", [cmake, "--build", "build", "--config", "Release"], "Mevcut build klasörünü Release olarak derler."),
            ])
        solution_files = sorted(path for path in root.glob("*.sln") if path.is_file() and not path.is_symlink())
        if solution_files:
            msbuild = shutil.which("msbuild")
            if msbuild:
                profiles.append(BuildProfile(
                    "Visual Studio çözümünü derle",
                    [msbuild, solution_files[0].name, "/m", "/p:Configuration=Release"],
                    "İlk .sln dosyasını MSBuild ile Release olarak derler.",
                ))
        if (root / "Cargo.toml").is_file():
            cargo = shutil.which("cargo") or "cargo"
            profiles.extend([
                BuildProfile("Cargo check", [cargo, "check"], "Rust projesini hızlıca doğrular."),
                BuildProfile("Cargo test", [cargo, "test"], "Rust testlerini çalıştırır."),
            ])
        if (root / "go.mod").is_file():
            go = shutil.which("go") or "go"
            profiles.extend([
                BuildProfile("Go build", [go, "build", "./..."], "Tüm Go paketlerini derler."),
                BuildProfile("Go test", [go, "test", "./..."], "Tüm Go testlerini çalıştırır."),
            ])
        if not profiles:
            profiles.append(BuildProfile(
                "Genel dosya doğrulaması",
                [self._python(), "-c", "from pathlib import Path; print(f'{sum(1 for p in Path(\".\").rglob(\"*\") if p.is_file())} dosya bulundu')"],
                "Bilinen build sistemi bulunamadığında çalışma alanını erişim açısından doğrular.",
            ))
        return profiles

    def run(self, profile: BuildProfile, timeout: int = 600) -> BuildResult:
        if not isinstance(profile, BuildProfile):
            raise TypeError("profile bir BuildProfile olmalıdır.")
        self._validate_profile(profile)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout pozitif bir sayı olmalıdır.")
        if not math.isfinite(float(timeout)) or timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout 0 ile {_MAX_TIMEOUT_SECONDS} arasında sonlu bir sayı olmalıdır.")
        root = self.workspace.require_root()
        allowed_profiles = self.detect_profiles()
        if profile not in allowed_profiles:
            raise WorkspaceError("Yalnızca proje için otomatik algılanan, önceden tanımlı build görevleri çalıştırılabilir.")
        try:
            completed = subprocess.run(
                profile.command,
                cwd=str(root),
                shell=False,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise WorkspaceError(f"Komut bulunamadı: {_safe_text(profile.command[0], limit=500)}") from exc
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            output = (stdout + "\n" + stderr).strip()
            raise WorkspaceError(f"Görev {timeout} saniye içinde tamamlanmadı.\n{output[-12000:]}") from exc
        stdout = completed.stdout if isinstance(completed.stdout, str) else ""
        stderr = completed.stderr if isinstance(completed.stderr, str) else ""
        output = (stdout + "\n" + stderr).strip()[-_MAX_OUTPUT_CHARS:]
        return_code = completed.returncode if isinstance(completed.returncode, int) and not isinstance(completed.returncode, bool) else -1
        return BuildResult(profile, return_code, output)

    def run_pipeline(self, stop_on_failure: bool = True) -> BuildPipelineResult:
        if not isinstance(stop_on_failure, bool):
            raise TypeError("stop_on_failure boolean olmalıdır.")
        results: list[BuildResult] = []
        for profile in self.detect_profiles():
            result = self.run(profile)
            results.append(result)
            if stop_on_failure and not result.succeeded:
                break
        return BuildPipelineResult(results)

    @staticmethod
    def _cancelled(cancel_check: Callable[[], bool] | None) -> bool:
        if cancel_check is None:
            return False
        try:
            return bool(cancel_check())
        except Exception:
            return False

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def run_live(
        self,
        profile: BuildProfile,
        timeout: int = 600,
        *,
        heartbeat: Callable[[int], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BuildResult:
        """Run one detected profile with elapsed-time heartbeats and cancellation."""
        if not isinstance(profile, BuildProfile):
            raise TypeError("profile bir BuildProfile olmalıdır.")
        self._validate_profile(profile)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout pozitif bir sayı olmalıdır.")
        if not math.isfinite(float(timeout)) or timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout 0 ile {_MAX_TIMEOUT_SECONDS} arasında sonlu bir sayı olmalıdır.")
        root = self.workspace.require_root()
        if profile not in self.detect_profiles():
            raise WorkspaceError(
                "Yalnızca proje için otomatik algılanan, önceden tanımlı build görevleri çalıştırılabilir."
            )
        try:
            process = subprocess.Popen(
                profile.command,
                cwd=str(root),
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise WorkspaceError(f"Komut bulunamadı: {_safe_text(profile.command[0], limit=500)}") from exc
        started = time.monotonic()
        last_heartbeat = -1
        stdout = ""
        stderr = ""
        try:
            while True:
                if self._cancelled(cancel_check):
                    self._terminate(process)
                    try:
                        stdout, stderr = process.communicate(timeout=1.0)
                    except (subprocess.TimeoutExpired, OSError):
                        stdout, stderr = "", ""
                    raise InterruptedError("Build/test kullanıcı tarafından iptal edildi.")
                elapsed = time.monotonic() - started
                if elapsed > float(timeout):
                    self._terminate(process)
                    try:
                        stdout, stderr = process.communicate(timeout=1.0)
                    except (subprocess.TimeoutExpired, OSError):
                        stdout, stderr = "", ""
                    output = ((stdout or "") + "\n" + (stderr or "")).strip()
                    raise WorkspaceError(
                        f"Görev {timeout} saniye içinde tamamlanmadı.\n{output[-12000:]}"
                    )
                try:
                    stdout, stderr = process.communicate(timeout=0.20)
                    break
                except subprocess.TimeoutExpired:
                    whole_seconds = int(elapsed)
                    if heartbeat is not None and whole_seconds != last_heartbeat:
                        last_heartbeat = whole_seconds
                        heartbeat(whole_seconds)
        except BaseException:
            if process.poll() is None:
                self._terminate(process)
            raise
        output = ((stdout or "") + "\n" + (stderr or "")).strip()[-_MAX_OUTPUT_CHARS:]
        return_code = process.returncode if isinstance(process.returncode, int) and not isinstance(process.returncode, bool) else -1
        return BuildResult(profile, return_code, output)

    def run_pipeline_live(
        self,
        stop_on_failure: bool = True,
        *,
        progress_callback: Callable[[BuildProgressEvent], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BuildPipelineResult:
        """Run detected profiles sequentially and report only real stage progress."""
        if not isinstance(stop_on_failure, bool):
            raise TypeError("stop_on_failure boolean olmalıdır.")
        profiles = self.detect_profiles()
        total = len(profiles)
        results: list[BuildResult] = []
        for index, profile in enumerate(profiles, start=1):
            if self._cancelled(cancel_check):
                raise InterruptedError("Build/test kullanıcı tarafından iptal edildi.")
            if progress_callback is not None:
                progress_callback(BuildProgressEvent(index - 1, total, profile.name, "başlatılıyor", 0))

            def heartbeat(elapsed: int, *, current=index, row=profile) -> None:
                if progress_callback is not None:
                    progress_callback(
                        BuildProgressEvent(current - 1, total, row.name, "çalışıyor", elapsed)
                    )

            result = self.run_live(
                profile,
                heartbeat=heartbeat,
                cancel_check=cancel_check,
            )
            results.append(result)
            if progress_callback is not None:
                progress_callback(
                    BuildProgressEvent(index, total, profile.name,
                                       "başarılı" if result.succeeded else "başarısız",
                                       0)
                )
            if stop_on_failure and not result.succeeded:
                break
        return BuildPipelineResult(results)

    @staticmethod
    def _validate_profile(profile: BuildProfile) -> None:
        if not isinstance(profile.name, str) or not profile.name.strip() or "\x00" in profile.name:
            raise WorkspaceError("Build profil adı geçerli bir metin olmalıdır.")
        if not isinstance(profile.description, str) or "\x00" in profile.description:
            raise WorkspaceError("Build profil açıklaması geçerli bir metin olmalıdır.")
        if not isinstance(profile.command, list) or not profile.command or len(profile.command) > _MAX_COMMAND_PARTS:
            raise WorkspaceError("Build komutu geçerli sayıda metin parçası içermelidir.")
        if any(
            not isinstance(part, str)
            or not part.strip()
            or "\x00" in part
            or len(part) > _MAX_COMMAND_PART_CHARS
            for part in profile.command
        ):
            raise WorkspaceError("Build komutu boş olmayan, güvenli metin parçalarından oluşmalıdır.")

    @staticmethod
    def _python() -> str:
        return shutil.which("python") or shutil.which("py") or "python"

    @staticmethod
    def _package_scripts(path: Path) -> dict[str, str]:
        try:
            if path.is_symlink() or not path.is_file():
                return {}
            data = read_json_object(path, max_bytes=1024 * 1024)
            scripts = data.get("scripts", {})
            if not isinstance(scripts, dict):
                return {}
            result: dict[str, str] = {}
            for name, command in scripts.items():
                if not isinstance(name, str) or not isinstance(command, str):
                    continue
                clean_name = name.strip()
                if not clean_name or "\x00" in clean_name or len(clean_name) > 200:
                    continue
                if "\x00" in command or len(command) > 20_000:
                    continue
                result[clean_name] = command
            return result
        except (OSError, UnicodeError, ValueError):
            return {}
