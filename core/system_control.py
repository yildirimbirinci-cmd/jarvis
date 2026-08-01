from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.workspace import WorkspaceService, WorkspaceError
from artmach_assistant.core.local_command_router import normalize_text, phrase_score
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

DISCOVERED_APPS_FILE = DATA_DIR / "discovered_applications.json"


@dataclass(frozen=True)
class DiscoveredApplication:
    query: str
    display_name: str
    executable: str
    process_name: str
    score: float


class SystemControlService:
    APP_ALIASES = {
        "calculator": ("hesap makinesi", "hesap makinasi", "calculator", "calc"),
        "notepad": ("not defteri", "notepad"),
        "vscode": ("visual studio code", "vs code", "vscode"),
        "visual_studio": ("visual studio",),
        "qt_creator": ("qt creator", "qtcreator"),
        "explorer": ("dosya gezgini", "explorer", "klasor"),
    }

    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace
        self.discovered_apps: dict[str, dict[str, str]] = {}
        self._catalog_lock = threading.RLock()
        self._load_discovered_apps()

    def _workspace_root(self) -> Path:
        try:
            return self.workspace.require_root()
        except Exception:
            return Path.home()

    @staticmethod
    def _desktop_roots() -> list[Path]:
        """Return the local Desktop locations Windows commonly uses.

        This is intentionally a tiny, non-recursive scope: Jarvis may open a
        folder the user explicitly names, but it does not crawl personal files
        or inspect their contents.
        """
        home = Path.home()
        candidates = [home / "Desktop", home / "OneDrive" / "Desktop"]
        for variable in ("OneDrive", "OneDriveConsumer", "USERPROFILE"):
            value = os.environ.get(variable, "").strip()
            if value:
                root = Path(value)
                candidates.append(root / "Desktop" if root.name.casefold() != "desktop" else root)
        unique: list[Path] = []
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved not in unique and resolved.is_dir():
                unique.append(resolved)
        return unique

    def open_desktop_folder(self, reference: str) -> str:
        """Open one explicitly requested, direct child folder of Desktop.

        No files are read and no recursive search is performed.  Ambiguous or
        missing names are reported instead of guessing a target.
        """
        if os.name != "nt":
            raise WorkspaceError("Masaüstü klasörü açma yalnızca Windows'ta destekleniyor.")
        query = normalize_text(reference)
        query = re.sub(r"^(?:su|bu|o)\s+", "", query).strip()
        query = re.sub(r"\s+(?:klasoru|klasor)$", "", query).strip()
        if len(query) < 2:
            return "Masaüstünde hangi klasörü açmamı istediğini söylemelisin."

        matches: list[tuple[float, Path]] = []
        for desktop in self._desktop_roots():
            try:
                children = list(desktop.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_dir():
                    continue
                name = normalize_text(child.name)
                if name == query:
                    matches.append((2.0, child))
                    continue
                score = phrase_score(query, name)
                if query in name or name in query:
                    score = max(score, 0.86)
                if score >= 0.78:
                    matches.append((score, child))
        if not matches:
            return f"Masaüstünde '{reference.strip()}' adlı bir klasör bulamadım."
        matches.sort(key=lambda row: row[0], reverse=True)
        best_score, best = matches[0]
        equally_good = [path for score, path in matches if score >= best_score - 0.03]
        if len(equally_good) > 1:
            return f"Masaüstünde '{reference.strip()}' için birden fazla klasör eşleşiyor; klasör adını biraz daha net söyle."
        try:
            os.startfile(str(best))
        except OSError as exc:
            raise WorkspaceError(f"Klasör açılamadı: {best} ({exc})") from exc
        return f"Masaüstündeki '{best.name}' klasörü açıldı."

    @staticmethod
    def _validated_application_row(value: object) -> dict[str, str] | None:
        if not isinstance(value, dict):
            return None
        required = ("display_name", "executable", "process_name")
        row: dict[str, str] = {}
        for field in required:
            item = value.get(field)
            if not isinstance(item, str) or not item.strip():
                return None
            row[field] = item.strip()
        query = value.get("query", "")
        row["query"] = query.strip() if isinstance(query, str) else ""
        return row

    def _load_discovered_apps(self) -> None:
        if not DISCOVERED_APPS_FILE.exists():
            self.discovered_apps = {}
            return
        try:
            raw = read_json_object(DISCOVERED_APPS_FILE, max_bytes=4 * 1024 * 1024)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            self.discovered_apps = {}
            return
        cleaned: dict[str, dict[str, str]] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            normalized_key = normalize_text(key)
            row = self._validated_application_row(value)
            if len(normalized_key) >= 2 and row is not None:
                cleaned[normalized_key] = row
        self.discovered_apps = cleaned

    def _save_discovered_apps(self) -> None:
        with self._catalog_lock:
            atomic_write_json(
                DISCOVERED_APPS_FILE,
                self.discovered_apps,
                max_bytes=4 * 1024 * 1024,
            )

    def refresh_application_catalog(self) -> str:
        """Build a local catalog from Windows' registered applications.

        This deliberately uses the App Paths registry only: it is fast, local,
        and does not crawl personal folders or transmit any information.
        """
        if os.name != "nt":
            return "Uygulama kataloğu yalnızca Windows'ta yenilenebilir."
        added = 0
        for display_name, executable in self._registry_candidates():
            process_name = executable.stem
            row = {
                "query": display_name,
                "display_name": display_name or process_name,
                "executable": str(executable),
                "process_name": process_name,
            }
            for label in {display_name, process_name}:
                key = normalize_text(label)
                if len(key) < 2:
                    continue
                if self.discovered_apps.get(key) != row:
                    self.discovered_apps[key] = row
                    added += 1
        if added:
            self._save_discovered_apps()
        return f"Uygulama kataloğu hazır: {len(self.discovered_apps)} yerel ad kaydı var."

    def register_application_alias(self, alias: str, application_reference: str) -> str:
        """Associate a user-provided spoken name with an already local app."""
        key = normalize_text(alias)
        if len(key) < 2:
            raise WorkspaceError("Uygulama adı çok kısa.")
        app = self.resolve_discovered_application(application_reference)
        if app is None:
            self.refresh_application_catalog()
            app = self.resolve_discovered_application(application_reference)
        if app is None:
            raise WorkspaceError(f"'{application_reference}' için yerel uygulama kaydı bulamadım.")
        self.discovered_apps[key] = {**app, "query": alias.strip()}
        self._save_discovered_apps()
        label = app.get("display_name") or Path(app.get("executable", "uygulama")).stem
        return f"Öğrendim. '{alias.strip()}' artık {label} uygulamasını ifade ediyor."

    def application_catalog(self, limit: int = 40) -> str:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit tam sayı olmalıdır.")
        if limit <= 0:
            return "Uygulama kataloğu:\n"
        unique: dict[str, str] = {}
        with self._catalog_lock:
            rows = list(self.discovered_apps.values())
        for row in rows:
            validated = self._validated_application_row(row)
            if validated is None:
                continue
            label = validated["display_name"]
            executable = validated["executable"]
            unique.setdefault(executable.casefold(), label)
        if not unique:
            return "Uygulama kataloğu henüz boş. Yenilememi veya bir uygulama aramamı isteyebilirsin."
        names = sorted(unique.values(), key=str.casefold)[:limit]
        suffix = "" if len(unique) <= limit else f"\n… ve {len(unique) - limit} uygulama daha."
        return "Yerel uygulama kataloğu:\n" + "\n".join(f"- {name}" for name in names) + suffix

    @staticmethod
    def _clean_discovery_query(command: str) -> str:
        text = normalize_text(command)
        patterns = (
            r"\bexe(?:\s+(?:si|sini))?\s+(?:bul|ara|find|locate)\b.*$",
            r"\b(?:uygulamasini|programini)\s+(?:bul|ara)\b.*$",
            r"\b(?:bul|ara|find|locate)\b.*$",
        )
        for pattern in patterns:
            text = re.sub(pattern, "", text).strip()
        return re.sub(r"\b(?:uygulama|program|exe)\b", " ", text).strip()

    @staticmethod
    def _query_tokens(query: str) -> list[str]:
        ignored = {"autodesk", "application", "program", "uygulama"}
        return [token for token in normalize_text(query).split() if token not in ignored and len(token) > 1]

    @staticmethod
    def _candidate_score(query: str, display_name: str, executable: Path) -> float:
        q = normalize_text(query)
        haystack = normalize_text(f"{display_name} {executable.stem} {executable.parent.name}")
        score = phrase_score(q, haystack)
        tokens = SystemControlService._query_tokens(query)
        if tokens:
            hits = sum(1 for token in tokens if token in haystack)
            score = max(score, hits / len(tokens))
        # Prefer exact 3ds Max executable names and matching requested year.
        if "3d" in q and "max" in q and executable.stem.casefold() in {"3dsmax", "3ds max"}:
            score += 0.35
        years = re.findall(r"\b20\d{2}\b", q)
        if years and any(year in haystack for year in years):
            score += 0.25
        return min(score, 1.5)

    def _registry_candidates(self) -> list[tuple[str, Path]]:
        if os.name != "nt":
            return []
        try:
            import winreg
        except ImportError:
            return []
        rows: list[tuple[str, Path]] = []
        roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
        views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
        app_paths = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for root in roots:
            for view in views:
                try:
                    with winreg.OpenKey(root, app_paths, 0, winreg.KEY_READ | view) as key:
                        count = winreg.QueryInfoKey(key)[0]
                        for index in range(count):
                            subname = winreg.EnumKey(key, index)
                            try:
                                with winreg.OpenKey(key, subname) as subkey:
                                    value, _ = winreg.QueryValueEx(subkey, None)
                                path = Path(str(value).strip('"'))
                                if path.suffix.casefold() == ".exe" and path.exists():
                                    rows.append((path.stem, path))
                            except OSError:
                                continue
                except OSError:
                    continue
        return rows

    def _start_menu_candidates(self) -> list[tuple[str, Path]]:
        if os.name != "nt":
            return []
        roots = [
            Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        ]
        shortcuts = [path for root in roots if root.exists() for path in root.rglob("*.lnk")]
        rows: list[tuple[str, Path]] = []
        for shortcut in shortcuts:
            escaped = str(shortcut).replace("'", "''")
            script = (
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + escaped + "');"
                "$s.TargetPath"
            )
            try:
                completed = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True, text=True, timeout=4,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                target = Path(completed.stdout.strip().strip('"'))
                if target.suffix.casefold() == ".exe" and target.exists():
                    rows.append((shortcut.stem, target))
            except (OSError, subprocess.SubprocessError):
                continue
        return rows

    def _filesystem_candidates(self, query: str) -> list[tuple[str, Path]]:
        if os.name != "nt":
            return []
        tokens = self._query_tokens(query)
        years = re.findall(r"\b20\d{2}\b", normalize_text(query))
        likely_names = {"3dsmax.exe"} if "max" in tokens else set()
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        ]
        rows: list[tuple[str, Path]] = []
        for root in roots:
            if not root.exists():
                continue
            # First try exact likely filenames for speed.
            for filename in likely_names:
                for path in root.rglob(filename):
                    rows.append((path.parent.name, path))
            # Targeted scan only in directories whose names match a query token/year.
            for current, dirs, files in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.relative_to(root).parts)
                if depth > 5:
                    dirs[:] = []
                    continue
                current_norm = normalize_text(str(current_path))
                relevant = any(token in current_norm for token in tokens) or any(year in current_norm for year in years)
                if not relevant and depth >= 2:
                    dirs[:] = [d for d in dirs if any(token in normalize_text(d) for token in tokens) or any(year in d for year in years)]
                for filename in files:
                    if not filename.casefold().endswith(".exe"):
                        continue
                    path = current_path / filename
                    if relevant or any(token in normalize_text(filename) for token in tokens):
                        rows.append((path.stem, path))
        return rows

    def find_application_executable(self, command: str) -> str:
        query = self._clean_discovery_query(command)
        if not query:
            raise WorkspaceError("Aranacak program adını anlayamadım.")
        if os.name != "nt":
            raise WorkspaceError("Program keşfi yalnızca Windows'ta destekleniyor.")

        candidates = self._registry_candidates() + self._start_menu_candidates() + self._filesystem_candidates(query)
        unique: dict[str, tuple[str, Path]] = {}
        for display, path in candidates:
            try:
                resolved = str(path.resolve()).casefold()
            except OSError:
                resolved = str(path).casefold()
            unique[resolved] = (display, path)

        ranked: list[DiscoveredApplication] = []
        for display, path in unique.values():
            score = self._candidate_score(query, display, path)
            if score >= 0.58:
                ranked.append(DiscoveredApplication(query, display or path.stem, str(path), path.stem, score))
        ranked.sort(key=lambda item: item.score, reverse=True)
        if not ranked:
            raise WorkspaceError(f"'{query}' için çalıştırılabilir dosya bulunamadı.")

        best = ranked[0]
        key = normalize_text(query)
        self.discovered_apps[key] = {
            "query": query,
            "display_name": best.display_name,
            "executable": best.executable,
            "process_name": best.process_name,
        }
        self._save_discovered_apps()
        return f"Buldum. {best.display_name} çalıştırılabilir dosyası: {best.executable}"

    def resolve_discovered_application(self, command: str) -> dict[str, str] | None:
        text = normalize_text(command)
        best: dict[str, str] | None = None
        best_score = 0.0
        for key, row in self.discovered_apps.items():
            score = 1.0 if key in text else phrase_score(text, key)
            if score > best_score:
                best, best_score = row, score
        return best if best and best_score >= 0.64 else None

    def infer_discovered_launch(self, command: str) -> tuple[str, str, str] | None:
        text = normalize_text(command)
        run_words = {"calistir", "ac", "baslat", "run", "launch", "open", "start"}
        if not set(text.split()) & run_words:
            return None
        app = self.resolve_discovered_application(command)
        if app is None:
            return None
        target = app["executable"]
        label = app.get("display_name") or Path(target).stem
        return "launch_discovered_app", target, f"{label} uygulamasını çalıştırmayı"

    @staticmethod
    def _fluent_action(text: str) -> str | None:
        tokens = normalize_text(text).split()
        if any(token.startswith(("kapat", "sonland", "durdur", "cik")) for token in tokens):
            return "close"
        if any(token.startswith(("ac", "calistir", "baslat", "calis")) for token in tokens):
            return "open"
        return None

    @staticmethod
    def _fluent_target(text: str) -> str:
        fillers = {"jarvis", "lutf", "lutfen", "bana", "bir", "de", "mi", "misin", "miyim", "sunu", "su", "onu", "onu", "artik", "hemen", "ya", "abi", "uygulamayi", "programi"}
        tokens = []
        for token in normalize_text(text).split():
            if token in fillers or token.startswith(("kapat", "sonland", "durdur", "cik", "ac", "calistir", "baslat", "calis")):
                continue
            tokens.append(token)
        return " ".join(tokens)

    def infer_fluent_action(self, command: str) -> tuple[str, str, str] | None:
        """Resolve natural Turkish open/close sentences to a safe local action."""
        action = self._fluent_action(command)
        target_phrase = self._fluent_target(command)
        if not action or not target_phrase:
            return None
        discovered = self.resolve_discovered_application(target_phrase)
        if discovered:
            label = discovered.get("display_name") or Path(discovered.get("executable", "program")).stem
            if action == "open":
                return "launch_discovered_app", discovered["executable"], f"{label} uygulamasını açmayı"
            return "close_observed_process", discovered.get("process_name") or Path(discovered["executable"]).stem, f"{label} uygulamasını kapatmayı"
        if action != "close":
            return None
        try:
            snapshot = self.process_snapshot()
        except Exception:
            return None
        best: tuple[str, float, str] | None = None
        for row in snapshot.values():
            name = row.get("name", "").strip()
            title = row.get("title", "").strip()
            if not name:
                continue
            score = max(phrase_score(target_phrase, name), phrase_score(target_phrase, title))
            if best is None or score > best[1]:
                best = (name, score, title or name)
        if best and best[1] >= 0.70:
            process, _score, label = best
            return "close_observed_process", process, f"{label} uygulamasını kapatmayı"
        return None

    def launch_discovered_application(self, target: str) -> str:
        path = Path(target.strip().strip('"'))
        if not path.exists() or path.suffix.casefold() != ".exe":
            raise WorkspaceError("Kaydedilen uygulama dosyası bulunamadı. Exe dosyasını yeniden bulmalısın.")
        try:
            subprocess.Popen([str(path)], cwd=str(path.parent))
        except OSError as exc:
            raise WorkspaceError(f"Uygulama çalıştırılamadı: {exc}") from exc
        return f"{path.stem} çalıştırıldı."


    def process_snapshot(self) -> dict[str, dict[str, str]]:
        """Return a UTF-8 Windows process/window snapshot keyed by PID.

        A modern Windows application may keep its process alive after its last
        visible window is closed. Therefore the snapshot also stores the main
        window handle/title, allowing observed learning to detect a window
        disappearing even when the PID remains alive.
        """
        if os.name != "nt":
            raise WorkspaceError("İşlem gözlemleme yalnızca Windows'ta destekleniyor.")
        script = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            "$paths=@{}; Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
            "ForEach-Object { if ($_.ExecutablePath) { $paths[[string]$_.ProcessId]=$_.ExecutablePath } }; "
            "Get-Process | ForEach-Object { [pscustomobject]@{ "
            "Id=$_.Id; ProcessName=$_.ProcessName; MainWindowTitle=$_.MainWindowTitle; "
            "MainWindowHandle=$_.MainWindowHandle; ExecutablePath=$paths[[string]$_.Id] } } | "
            "ConvertTo-Json -Compress -Depth 3"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError("Çalışan uygulamalar okunurken zaman aşımı oluştu.") from exc
        if completed.returncode != 0:
            raise WorkspaceError((completed.stderr or completed.stdout).strip() or "Çalışan işlemler okunamadı.")
        raw = completed.stdout.lstrip("\ufeff").strip()
        if not raw:
            return {}
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorkspaceError(f"İşlem listesi çözümlenemedi: {exc}") from exc
        if isinstance(rows, dict):
            rows = [rows]
        snapshot: dict[str, dict[str, str]] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("Id") is None:
                continue
            pid = str(row["Id"])
            name = str(row.get("ProcessName") or "").strip()
            title = str(row.get("MainWindowTitle") or "").strip()
            try:
                handle = int(row.get("MainWindowHandle") or 0)
            except (TypeError, ValueError):
                handle = 0
            if name:
                snapshot[pid] = {
                    "name": name,
                    "title": title,
                    "visible": "1" if handle and title else "0",
                    "path": str(row.get("ExecutablePath") or "").strip(),
                }
        return snapshot

    @staticmethod
    def detect_observed_process_change(
        before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]
    ) -> tuple[str, str, str] | None:
        ignored = {
            "conhost", "powershell", "pwsh", "cmd", "python", "pythonw",
            "searchhost", "shellexperiencehost", "startmenuexperiencehost",
            "runtimebroker", "applicationframehost",
        }
        closed: list[tuple[str, str]] = []
        opened: list[tuple[str, str, str]] = []

        # A newly started user application can be learned safely only when
        # Windows supplies the executable path. Arbitrary mouse clicks and UI
        # edits are intentionally not guessed from a process snapshot.
        for pid in set(after) - set(before):
            row = after[pid]
            name = row.get("name", "").strip()
            title = row.get("title", "").strip()
            path = row.get("path", "").strip()
            visible = row.get("visible") == "1"
            if name and path and name.casefold() not in ignored and (visible or title):
                opened.append((path, name, title or name))

        # 1) A process really ended.
        for pid in set(before) - set(after):
            row = before[pid]
            name = row.get("name", "").strip()
            title = row.get("title", "").strip()
            visible = row.get("visible") == "1"
            if not name or name.casefold() in ignored:
                continue
            if visible or title or name.casefold() in {"calculatorapp", "calculator", "notepad", "code", "devenv", "qtcreator"}:
                closed.append((name, title or name))

        # 2) PID remains, but its visible application window disappeared.
        for pid in set(before) & set(after):
            old = before[pid]
            new = after[pid]
            name = old.get("name", "").strip()
            if not name or name.casefold() in ignored:
                continue
            old_visible = old.get("visible") == "1" or bool(old.get("title", "").strip())
            new_visible = new.get("visible") == "1" or bool(new.get("title", "").strip())
            if old_visible and not new_visible:
                closed.append((name, old.get("title", "").strip() or name))

        unique: dict[str, tuple[str, str]] = {}
        for name, title in closed:
            unique[name.casefold()] = (name, title)
        if len(unique) != 1:
            unique_opened: dict[str, tuple[str, str, str]] = {}
            for path, name, title in opened:
                unique_opened[path.casefold()] = (path, name, title)
            if len(unique_opened) != 1:
                return None
            path, _name, title = next(iter(unique_opened.values()))
            return "opened", path, title
        name, title = next(iter(unique.values()))
        return "closed", name, title

    def close_process_by_name(self, target: str) -> str:
        process_name = Path(target.strip().strip('"')).stem
        if not process_name:
            raise WorkspaceError("Kaydedilen işlem adı geçersiz.")
        if os.name != "nt":
            raise WorkspaceError("Uygulama kapatma yalnızca Windows'ta destekleniyor.")
        escaped = process_name.replace("'", "''")
        script = (
            f"$p = Get-Process -Name '{escaped}' -ErrorAction SilentlyContinue; "
            "if ($p) { $p | Stop-Process -Force; exit 0 } else { exit 3 }"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode == 3:
            return f"{process_name} zaten kapalı."
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise WorkspaceError(f"{process_name} kapatılamadı: {detail or 'bilinmeyen hata'}")
        return f"{process_name} kapatıldı."

    def infer_safe_action(self, command: str) -> tuple[str, str, str] | None:
        text = normalize_text(command)
        close_words = ("kapat", "sonlandir", "durdur", "close", "quit", "terminate", "exit")
        open_words = ("ac", "baslat", "calistir", "open", "launch", "start", "run")
        action = "close" if any(word in text.split() for word in close_words) else None
        if action is None and any(word in text.split() for word in open_words):
            action = "open"
        if action is None:
            return None

        best_key = ""
        best_score = 0.0
        for key, aliases in self.APP_ALIASES.items():
            for alias in aliases:
                normalized_alias = normalize_text(alias)
                score = 1.0 if normalized_alias in text else phrase_score(text, normalized_alias)
                if score > best_score:
                    best_key, best_score = key, score
        if not best_key or best_score < 0.62:
            return None

        labels = {
            "calculator": "Hesap Makinesi", "notepad": "Not Defteri",
            "vscode": "Visual Studio Code", "visual_studio": "Visual Studio",
            "qt_creator": "Qt Creator", "explorer": "Dosya Gezgini",
        }
        label = labels[best_key]
        if action == "close":
            return "close_system_app", f"{label} kapat", f"{label}'ni kapatmayı"
        return "open_system_app", f"{label} aç", f"{label}'ni açmayı"

    def close_application(self, command: str) -> str:
        text = normalize_text(command)
        app_key = ""
        best_score = 0.0
        for key, aliases in self.APP_ALIASES.items():
            for alias in aliases:
                normalized_alias = normalize_text(alias)
                score = 1.0 if normalized_alias in text else phrase_score(text, normalized_alias)
                if score > best_score:
                    app_key, best_score = key, score
        if not app_key or best_score < 0.62:
            raise WorkspaceError("Kapatılacak uygulamayı anlayamadım.")

        process_map = {
            "calculator": ("CalculatorApp", "Calculator"), "notepad": ("notepad",),
            "vscode": ("Code",), "visual_studio": ("devenv",),
            "qt_creator": ("qtcreator",), "explorer": ("explorer",),
        }
        labels = {
            "calculator": "Hesap Makinesi", "notepad": "Not Defteri",
            "vscode": "Visual Studio Code", "visual_studio": "Visual Studio",
            "qt_creator": "Qt Creator", "explorer": "Dosya Gezgini",
        }
        names = process_map[app_key]
        if os.name != "nt":
            raise WorkspaceError("Uygulama kapatma komutu yalnızca Windows'ta destekleniyor.")
        quoted = ",".join(f"'{name}'" for name in names)
        script = f"$p = Get-Process -Name {quoted} -ErrorAction SilentlyContinue; if ($p) {{ $p | Stop-Process -Force; exit 0 }} else {{ exit 3 }}"
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        label = labels[app_key]
        if completed.returncode == 3:
            return f"{label} zaten kapalı."
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise WorkspaceError(f"{label} kapatılamadı: {detail or 'bilinmeyen hata'}")
        return f"{label} kapatıldı."

    def execute(self, command: str) -> str:
        text = normalize_text(command)
        root = self._workspace_root()
        folder_aliases = ("explorer", "klasoru ac", "proje klasorunu ac", "dosya gezginini ac")
        if any(key in text for key in folder_aliases) or max(phrase_score(text, key) for key in folder_aliases) >= 0.76:
            os.startfile(str(root))
            return f"Klasör açıldı: {root}"

        apps = [
            (("visual studio code", "vs code", "vscode"), ["code", str(root)], "Visual Studio Code"),
            (("visual studio",), ["devenv", str(root)], "Visual Studio"),
            (("qt creator", "qtcreator"), ["qtcreator", str(root)], "Qt Creator"),
            (("not defteri", "notepad"), ["notepad"], "Not Defteri"),
            (("hesap makinesi", "hesap makinasi", "calculator", "calc"), ["calc"], "Hesap Makinesi"),
        ]
        for aliases, args, label in apps:
            normalized_aliases = tuple(normalize_text(alias) for alias in aliases)
            if any(alias in text for alias in normalized_aliases) or max(phrase_score(text, alias) for alias in normalized_aliases) >= 0.73:
                exe = shutil.which(args[0]) or args[0]
                try:
                    if os.name == "nt" and args[0].casefold() == "calc":
                        subprocess.Popen(["cmd", "/c", "start", "", "calc.exe"], cwd=str(root), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    else:
                        subprocess.Popen([exe, *args[1:]], cwd=str(root))
                except OSError as exc:
                    raise WorkspaceError(f"Uygulama açılamadı: {label} ({exc})") from exc
                return f"{label} açıldı."
        raise WorkspaceError("Bu komut yerel izin verilen komutlar arasında değil.")
