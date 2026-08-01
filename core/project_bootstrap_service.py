from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from artmach_assistant.core.operation_control import OperationCancelled, OperationController
from artmach_assistant.core.workspace import WorkspaceError

_MAX_PROJECT_NAME = 80
_MAX_GOAL = 4000
_MAX_FILES = 48
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024
_VALID_TEMPLATES = {"python_desktop", "python_cli", "python_library"}
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_SAFE_NAME = re.compile(r"[^0-9A-Za-z_]+")


@dataclass(frozen=True, slots=True)
class ProjectBootstrapFile:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ProjectBootstrapPlan:
    creation_id: str
    project_name: str
    package_name: str
    template: str
    parent: str
    root: str
    goal: str
    files: tuple[ProjectBootstrapFile, ...]
    initial_requirements: tuple[str, ...]
    initial_decisions: tuple[str, ...]
    initial_acceptance: tuple[str, ...]
    initial_tasks: tuple[str, ...]

    def report(self) -> str:
        template_labels = {
            "python_desktop": "Python / PySide6 masaüstü",
            "python_cli": "Python komut satırı",
            "python_library": "Python kütüphane",
        }
        lines = [
            "YENİ PROJE OLUŞTURMA TASLAĞI",
            f"Kimlik: {self.creation_id}",
            f"Proje: {self.project_name}",
            f"Tür: {template_labels.get(self.template, self.template)}",
            f"Hedef klasör: {self.root}",
            f"Ana hedef: {self.goal}",
            f"Oluşturulacak dosya sayısı: {len(self.files)}",
            "Başlangıç dosyaları:",
        ]
        lines.extend(f"- {item.path}" for item in self.files)
        lines.extend(
            [
                "",
                "Henüz hiçbir klasör veya dosya oluşturulmadı.",
                "Uygulamak için açıkça 'yeni proje taslağını uygula' demelisin.",
            ]
        )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ProjectBootstrapResult:
    creation_id: str
    root: str
    created_files: tuple[str, ...]
    validation_output: str

    def report(self) -> str:
        return (
            f"Yeni proje oluşturuldu: {self.root}. "
            f"{len(self.created_files)} dosya atomik olarak yazıldı. "
            "Başlangıç sözdizimi ve test doğrulaması başarılı."
        )


class ProjectBootstrapService:
    """Create a bounded local project only after an explicit approval.

    The service never writes during :meth:`plan`.  :meth:`apply` first builds
    and validates a sibling temporary tree, then atomically renames it into
    place.  A failure or cancellation therefore cannot leave a half-created
    project at the requested destination.
    """

    def __init__(self, *, python_executable: str | None = None) -> None:
        self.python_executable = str(python_executable or sys.executable)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _clean_project_name(value: str) -> str:
        name = " ".join(str(value or "").strip().split())
        if not name or len(name) > _MAX_PROJECT_NAME:
            raise WorkspaceError("Proje adı 1 ile 80 karakter arasında olmalıdır.")
        if any(char in name for char in ("/", "\\", "\x00")) or name in {".", ".."}:
            raise WorkspaceError("Proje adı yol karakterleri içeremez.")
        if name.rstrip(" .") != name:
            raise WorkspaceError("Proje adı nokta veya boşlukla bitemez.")
        if name.casefold() in _WINDOWS_RESERVED:
            raise WorkspaceError("Bu proje adı Windows tarafından ayrılmıştır.")
        return name

    @staticmethod
    def package_name(project_name: str) -> str:
        normalized = str(project_name).strip().replace("-", "_").replace(" ", "_")
        normalized = _SAFE_NAME.sub("_", normalized).strip("_").casefold()
        normalized = re.sub(r"_+", "_", normalized)
        if not normalized:
            normalized = "new_project"
        if normalized[0].isdigit():
            normalized = "project_" + normalized
        if normalized.casefold() in _WINDOWS_RESERVED:
            normalized += "_project"
        return normalized[:64]

    @staticmethod
    def _safe_parent(parent: str | Path) -> Path:
        candidate = Path(parent).expanduser()
        if candidate.is_symlink():
            raise WorkspaceError("Yeni proje üst klasörü sembolik bağlantı olamaz.")
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WorkspaceError(f"Yeni proje üst klasörü bulunamadı: {candidate}") from exc
        if not resolved.is_dir() or resolved.is_symlink():
            raise WorkspaceError("Yeni proje üst yolu gerçek bir klasör olmalıdır.")
        return resolved

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        raw = str(value or "").replace("\\", "/").strip()
        path = PurePosixPath(raw)
        if (
            not raw
            or path.is_absolute()
            or ".." in path.parts
            or any(part in {"", "."} for part in path.parts)
            or "\x00" in raw
        ):
            raise WorkspaceError(f"Güvensiz proje dosya yolu: {value}")
        return path.as_posix()

    @staticmethod
    def _creation_id(root: Path, template: str, goal: str) -> str:
        digest = hashlib.sha256(
            f"{root}|{template}|{goal}".encode("utf-8")
        ).hexdigest()[:10]
        return f"NEW-{digest.upper()}"

    def plan(
        self,
        *,
        project_name: str,
        parent: str | Path,
        template: str = "python_desktop",
        goal: str = "",
    ) -> ProjectBootstrapPlan:
        name = self._clean_project_name(project_name)
        template_key = str(template or "").strip().casefold()
        if template_key not in _VALID_TEMPLATES:
            raise WorkspaceError(
                "Desteklenen başlangıç türleri: python_desktop, python_cli, python_library."
            )
        parent_path = self._safe_parent(parent)
        root = (parent_path / name).resolve(strict=False)
        if root.parent != parent_path:
            raise WorkspaceError("Yeni proje üst klasörün doğrudan altında oluşturulmalıdır.")
        if root.exists():
            raise WorkspaceError(f"Hedef proje klasörü zaten var: {root}")
        package = self.package_name(name)
        clean_goal = " ".join(str(goal or "").strip().split())[:_MAX_GOAL]
        if not clean_goal:
            clean_goal = f"{name} adlı yerel uygulamayı güvenli ve test edilebilir biçimde geliştirmek."
        creation_id = self._creation_id(root, template_key, clean_goal)
        files = self._template_files(
            project_name=name,
            package_name=package,
            template=template_key,
            goal=clean_goal,
            creation_id=creation_id,
        )
        self._validate_file_set(files)
        requirements, decisions, acceptances, tasks = self._initial_memory(
            name, template_key
        )
        return ProjectBootstrapPlan(
            creation_id=creation_id,
            project_name=name,
            package_name=package,
            template=template_key,
            parent=str(parent_path),
            root=str(root),
            goal=clean_goal,
            files=files,
            initial_requirements=requirements,
            initial_decisions=decisions,
            initial_acceptance=acceptances,
            initial_tasks=tasks,
        )

    @staticmethod
    def _initial_memory(
        project_name: str, template: str
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        requirements = (
            "Uygulama yerel olarak çalışmalı ve ağ bağlantısı olmadan temel işlevini sürdürebilmeli.",
            "Her değişiklik otomatik test veya build doğrulamasından geçmeli.",
            "Hatalar kullanıcıya anlaşılır biçimde bildirilmeli ve uygulama kontrolsüz kapanmamalı.",
        )
        if template == "python_desktop":
            decisions = (
                "Python 3.11+, src yerleşimi ve PySide6 masaüstü arayüzü kullanılacak.",
                "Arayüz, iş mantığı ve kalıcı veri katmanları birbirinden ayrılacak.",
            )
            tasks = (
                "Ana pencereyi ve uygulama yaşam döngüsünü proje hedeflerine göre tamamla",
                "Ana iş mantığını arayüzden bağımsız servisler olarak geliştir",
                "Yapılandırma, hata yönetimi ve kalıcı veri davranışlarını ekle",
                "Otomatik testleri ve kullanıcı kabul senaryolarını tamamla",
            )
        elif template == "python_cli":
            decisions = (
                "Python 3.11+ ve src yerleşimi kullanılacak.",
                "Komut satırı ayrıştırma katmanı ana iş mantığından ayrılacak.",
            )
            tasks = (
                "Komut satırı seçeneklerini ve yardım metinlerini proje hedeflerine göre tamamla",
                "Ana iş mantığını bağımsız servisler olarak geliştir",
                "Yapılandırma ve hata yönetimi davranışlarını ekle",
                "Otomatik testleri ve kullanıcı kabul senaryolarını tamamla",
            )
        else:
            decisions = (
                "Python 3.11+, src yerleşimi ve küçük bir genel API kullanılacak.",
                "Genel API ile iç uygulama ayrıntıları birbirinden ayrılacak.",
            )
            tasks = (
                "Kütüphanenin genel API yüzeyini proje hedeflerine göre tanımla",
                "Ana iş mantığını ve veri modellerini geliştir",
                "Hata sözleşmelerini ve geriye uyumluluk kurallarını ekle",
                "Otomatik testleri, örnekleri ve kullanım belgelerini tamamla",
            )
        acceptances = (
            "Bütün Python dosyaları sözdizimi doğrulamasından geçmeli.",
            "Otomatik testlerin tamamı başarılı olmalı.",
            f"{project_name} temiz bir Python sürecinde temel başlangıç kontrolünü geçmeli.",
        )
        return requirements, decisions, acceptances, tasks

    def _template_files(
        self,
        *,
        project_name: str,
        package_name: str,
        template: str,
        goal: str,
        creation_id: str,
    ) -> tuple[ProjectBootstrapFile, ...]:
        dependency = '\n  "PySide6>=6.6",' if template == "python_desktop" else ""
        scripts = ""
        if template in {"python_desktop", "python_cli"}:
            scripts = f'\n[project.scripts]\n{package_name.replace("_", "-")} = "{package_name}.__main__:main"\n'
        pyproject = f'''[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n\n[project]\nname = "{package_name.replace("_", "-")}"\nversion = "0.1.0"\ndescription = {json.dumps(goal, ensure_ascii=False)}\nrequires-python = ">=3.11"\ndependencies = [{dependency}\n]\n{scripts}\n[tool.setuptools.packages.find]\nwhere = ["src"]\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'''
        common = [
            ProjectBootstrapFile(
                ".gitignore",
                "__pycache__/\n*.py[cod]\n.pytest_cache/\n.venv/\ndist/\nbuild/\n*.egg-info/\n.env\n",
            ),
            ProjectBootstrapFile(
                "README.md",
                f"# {project_name}\n\n{goal}\n\n## Başlangıç\n\n```powershell\npython -m pip install -e .\npython -m pytest -q\n```\n",
            ),
            ProjectBootstrapFile("pyproject.toml", pyproject),
            ProjectBootstrapFile(
                f"src/{package_name}/__init__.py",
                f'"""{project_name} package."""\n\n__version__ = "0.1.0"\n',
            ),
            ProjectBootstrapFile(
                f"src/{package_name}/core.py",
                f'''from __future__ import annotations\n\n\ndef application_summary() -> str:\n    return {json.dumps(project_name + ": " + goal, ensure_ascii=False)}\n''',
            ),
            ProjectBootstrapFile(
                "tests/test_core.py",
                f'''from {package_name}.core import application_summary\n\n\ndef test_application_summary_mentions_project() -> None:\n    assert {json.dumps(project_name, ensure_ascii=False)} in application_summary()\n''',
            ),
            ProjectBootstrapFile(
                "docs/architecture.md",
                f"# Mimari\n\n## Hedef\n\n{goal}\n\n## Katmanlar\n\n- Giriş / kullanıcı etkileşimi\n- Uygulama servisleri\n- Veri ve dış sistem adaptörleri\n- Otomatik testler\n",
            ),
        ]
        if template == "python_desktop":
            common.extend(
                [
                    ProjectBootstrapFile(
                        f"src/{package_name}/app.py",
                        f'''from __future__ import annotations\n\nimport sys\n\nfrom .core import application_summary\n\n\ndef run() -> int:\n    try:\n        from PySide6.QtWidgets import QApplication, QLabel, QMainWindow\n    except ImportError as exc:\n        raise RuntimeError("PySide6 kurulu değil. 'python -m pip install -e .' çalıştır.") from exc\n\n    application = QApplication.instance() or QApplication(sys.argv)\n    window = QMainWindow()\n    window.setWindowTitle({json.dumps(project_name, ensure_ascii=False)})\n    window.setCentralWidget(QLabel(application_summary()))\n    window.resize(900, 600)\n    window.show()\n    return int(application.exec())\n''',
                    ),
                    ProjectBootstrapFile(
                        f"src/{package_name}/__main__.py",
                        '''from __future__ import annotations\n\nfrom .app import run\n\n\ndef main() -> int:\n    return run()\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
                    ),
                ]
            )
        elif template == "python_cli":
            common.extend(
                [
                    ProjectBootstrapFile(
                        f"src/{package_name}/cli.py",
                        f'''from __future__ import annotations\n\nimport argparse\n\nfrom .core import application_summary\n\n\ndef build_parser() -> argparse.ArgumentParser:\n    parser = argparse.ArgumentParser(prog={json.dumps(package_name.replace("_", "-"))})\n    parser.add_argument("--version", action="store_true", help="Sürüm bilgisini göster")\n    return parser\n\n\ndef run(argv: list[str] | None = None) -> int:\n    arguments = build_parser().parse_args(argv)\n    if arguments.version:\n        print("0.1.0")\n    else:\n        print(application_summary())\n    return 0\n''',
                    ),
                    ProjectBootstrapFile(
                        f"src/{package_name}/__main__.py",
                        '''from __future__ import annotations\n\nfrom .cli import run\n\n\ndef main() -> int:\n    return run()\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
                    ),
                    ProjectBootstrapFile(
                        "tests/test_cli.py",
                        f'''from {package_name}.cli import run\n\n\ndef test_cli_runs(capsys) -> None:\n    assert run([]) == 0\n    assert {json.dumps(project_name, ensure_ascii=False)} in capsys.readouterr().out\n''',
                    ),
                ]
            )
        else:
            common.append(
                ProjectBootstrapFile(
                    f"src/{package_name}/api.py",
                    '''from __future__ import annotations\n\nfrom .core import application_summary\n\n\ndef describe() -> str:\n    return application_summary()\n''',
                )
            )
        metadata = {
            "schema_version": 1,
            "creation_id": creation_id,
            "project_name": project_name,
            "package_name": package_name,
            "template": template,
            "goal": goal,
            "created_at": self._now_iso(),
        }
        common.append(
            ProjectBootstrapFile(
                ".jarvis/project.json",
                json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            )
        )
        return tuple(common)

    def _validate_file_set(self, files: Iterable[ProjectBootstrapFile]) -> None:
        rows = tuple(files)
        if not rows or len(rows) > _MAX_FILES:
            raise WorkspaceError("Proje iskeleti güvenli dosya sayısı sınırını aşıyor.")
        seen: set[str] = set()
        total = 0
        for item in rows:
            path = self._safe_relative_path(item.path)
            if path.casefold() in seen:
                raise WorkspaceError(f"Proje iskeletinde yinelenen dosya yolu: {path}")
            seen.add(path.casefold())
            encoded = str(item.content).encode("utf-8")
            if len(encoded) > _MAX_FILE_BYTES:
                raise WorkspaceError(f"Proje iskeletindeki dosya çok büyük: {path}")
            total += len(encoded)
        if total > _MAX_TOTAL_BYTES:
            raise WorkspaceError("Proje iskeleti toplam boyut sınırını aşıyor.")

    @staticmethod
    def _progress(
        operation: OperationController | None,
        *,
        phase: str,
        current: int,
        total: int,
        detail: str,
    ) -> None:
        if operation is None:
            return
        operation.checkpoint()
        operation.update(phase=phase, current=current, total=total, detail=detail)

    def apply(
        self,
        plan: ProjectBootstrapPlan,
        *,
        operation: OperationController | None = None,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> ProjectBootstrapResult:
        if not isinstance(plan, ProjectBootstrapPlan):
            raise TypeError("plan bir ProjectBootstrapPlan olmalıdır.")
        parent = self._safe_parent(plan.parent)
        root = Path(plan.root).expanduser().resolve(strict=False)
        if root.parent != parent or root.name != plan.project_name:
            raise WorkspaceError("Proje taslağının hedef yolu sonradan değişmiş.")
        if root.exists():
            raise WorkspaceError(f"Hedef proje klasörü artık mevcut: {root}")
        expected_id = self._creation_id(root, plan.template, plan.goal)
        if expected_id != plan.creation_id:
            raise WorkspaceError("Proje oluşturma taslağının kimliği geçersiz.")
        self._validate_file_set(plan.files)

        total_steps = len(plan.files) + 3
        if operation is not None:
            operation.start(
                "Yeni proje oluşturma",
                phase="Geçici proje hazırlanıyor",
                total=total_steps,
            )
        temp_root = Path(
            tempfile.mkdtemp(prefix=f".jarvis_{plan.package_name}_", dir=str(parent))
        )
        committed = False
        try:
            for index, item in enumerate(plan.files, start=1):
                self._progress(
                    operation,
                    phase="Başlangıç dosyaları yazılıyor",
                    current=index,
                    total=total_steps,
                    detail=item.path,
                )
                if progress_callback is not None:
                    progress_callback("write", index, total_steps, item.path)
                relative = self._safe_relative_path(item.path)
                target = (temp_root / relative).resolve(strict=False)
                if temp_root not in target.parents:
                    raise WorkspaceError(f"Proje dosyası geçici kökün dışına çıkıyor: {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item.content, encoding="utf-8", newline="\n")

            self._progress(
                operation,
                phase="Python kaynakları doğrulanıyor",
                current=len(plan.files) + 1,
                total=total_steps,
                detail="Sözdizimi kontrolü",
            )
            syntax_output = self._validate_python_sources(temp_root)

            self._progress(
                operation,
                phase="Başlangıç testleri çalıştırılıyor",
                current=len(plan.files) + 2,
                total=total_steps,
                detail="Otomatik testler",
            )
            test_output = self._run_tests(temp_root)
            self._remove_generated_caches(temp_root)

            self._progress(
                operation,
                phase="Proje atomik olarak kaydediliyor",
                current=total_steps,
                total=total_steps,
                detail=str(root),
            )
            os.replace(temp_root, root)
            committed = True
            if operation is not None:
                operation.finish(detail=str(root))
            return ProjectBootstrapResult(
                creation_id=plan.creation_id,
                root=str(root),
                created_files=tuple(item.path for item in plan.files),
                validation_output=(syntax_output + "\n" + test_output).strip()[-20000:],
            )
        except OperationCancelled:
            if operation is not None:
                operation.finish(detail="Kullanıcı tarafından iptal edildi")
            raise
        except Exception:
            if operation is not None:
                operation.finish(detail="Proje oluşturulamadı; geçici dosyalar temizlendi")
            raise
        finally:
            if not committed:
                shutil.rmtree(temp_root, ignore_errors=True)

    @staticmethod
    def _validate_python_sources(root: Path) -> str:
        count = 0
        for path in sorted(root.rglob("*.py")):
            if path.is_symlink() or not path.is_file():
                raise WorkspaceError(f"Güvensiz Python kaynağı: {path}")
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                raise WorkspaceError(f"Başlangıç Python sözdizimi doğrulanamadı: {exc}") from exc
            count += 1
        if count < 1:
            raise WorkspaceError("Başlangıç projesinde Python kaynağı bulunamadı.")
        return f"{count} Python dosyası sözdizimi kontrolünden geçti."

    def _run_tests(self, root: Path) -> str:
        environment = os.environ.copy()
        source_root = str(root / "src")
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = source_root + (os.pathsep + existing if existing else "")
        if importlib.util.find_spec("pytest") is not None:
            command = [self.python_executable, "-m", "pytest", "-q"]
        else:
            command = [self.python_executable, "-m", "unittest", "discover", "-s", "tests", "-q"]
        try:
            completed = subprocess.run(
                command,
                cwd=str(root),
                shell=False,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=120,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError(f"Başlangıç testleri çalıştırılamadı: {exc}") from exc
        output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        if completed.returncode != 0:
            raise WorkspaceError(
                "Başlangıç testleri başarısız olduğu için proje oluşturulmadı.\n"
                + output[-12000:]
            )
        return output[-12000:] or "Başlangıç testleri başarılı."

    @staticmethod
    def _remove_generated_caches(root: Path) -> None:
        for directory in tuple(root.rglob("__pycache__")) + tuple(root.rglob(".pytest_cache")):
            if directory.is_dir() and not directory.is_symlink():
                shutil.rmtree(directory, ignore_errors=True)
        for path in root.rglob("*.py[co]"):
            try:
                path.unlink()
            except OSError:
                pass
