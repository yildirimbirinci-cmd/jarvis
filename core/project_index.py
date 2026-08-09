from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath
import os
import re


TEXT_EXTENSIONS = {
    ".py", ".pyw", ".qml", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt",
    ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".cs", ".java", ".kt", ".go", ".rs",
    ".cmake", ".pro", ".pri", ".qrc", ".ui", ".xml", ".yml", ".yaml", ".toml", ".ini",
    ".cfg", ".bat", ".cmd", ".ps1", ".sh", ".html", ".css", ".scss", ".sql",
}

IGNORED_DIRS = {
    ".git", ".svn", ".hg", ".idea", ".vs", ".vscode", ".venv", "venv", "env",
    "node_modules", "__pycache__", "build", "dist", "out", "target", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "coverage", ".next", ".nuxt",
    ".jarvis_fix_backup", ".artmach_assistant", ".jarvis",
}


def _normalize_relative_path(value: str | Path) -> Path | None:
    """Return a safe, normalized project-relative path."""
    raw = str(value).strip().replace("\\", "/")
    if not raw:
        return None
    candidate = Path(raw)
    windows_candidate = PureWindowsPath(raw)
    if candidate.is_absolute() or candidate.drive or windows_candidate.is_absolute() or windows_candidate.drive:
        return None
    parts: list[str] = []
    for part in PurePath(raw).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None
    return Path(*parts)


def _relative_path_key(value: str | Path) -> str | None:
    normalized = _normalize_relative_path(value)
    if normalized is None:
        return None
    return os.path.normcase(os.path.normpath(str(normalized)))


@dataclass
class IndexedFile:
    relative_path: str
    suffix: str
    size: int


@dataclass
class ProjectIndex:
    root: Path
    files: list[IndexedFile] = field(default_factory=list)
    extension_counts: Counter[str] = field(default_factory=Counter)
    markers: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, root: str | Path, payload: dict[str, object]) -> "ProjectIndex":
        root_path = Path(root).expanduser().resolve(strict=False)
        files_by_key: dict[str, IndexedFile] = {}
        raw_files = payload.get("files", [])
        if isinstance(raw_files, list):
            for raw in raw_files:
                if not isinstance(raw, dict):
                    continue
                relative = _normalize_relative_path(str(raw.get("relative_path", "")))
                suffix = str(raw.get("suffix", "")).strip() or "[uzantısız]"
                try:
                    size = max(0, int(raw.get("size", 0)))
                except (TypeError, ValueError):
                    continue
                if relative is not None:
                    relative_path = str(relative)
                    key = _relative_path_key(relative_path)
                    if key is not None:
                        files_by_key.setdefault(key, IndexedFile(relative_path, suffix, size))
        files = list(files_by_key.values())
        index = cls(root=root_path, files=sorted(files, key=lambda item: item.relative_path.casefold()))
        index.extension_counts = Counter(item.suffix for item in index.files)
        index.markers = index._detect_markers()
        return index

    def to_dict(self) -> dict[str, object]:
        return {
            "files": [
                {
                    "relative_path": item.relative_path,
                    "suffix": item.suffix,
                    "size": item.size,
                }
                for item in self.files
            ]
        }

    @classmethod
    def build(cls, root: str | Path, max_files: int = 12000) -> "ProjectIndex":
        root = Path(root).expanduser().resolve(strict=False)
        if isinstance(max_files, bool):
            max_files = 12000
        try:
            max_files = max(1, int(max_files))
        except (TypeError, ValueError, OverflowError):
            max_files = 12000

        index = cls(root=root)
        for path in sorted(root.rglob("*")):
            if len(index.files) >= max_files:
                break
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            # Only inspect the path inside the project. A project may itself live
            # below a parent directory named build/out/target without being ignored.
            if any(part in IGNORED_DIRS for part in relative.parts[:-1]):
                continue
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            suffix = path.suffix.lower() or "[uzantısız]"
            normalized = _normalize_relative_path(relative)
            if normalized is None:
                continue
            index.files.append(IndexedFile(str(normalized), suffix, size))
            index.extension_counts[suffix] += 1
        index.markers = index._detect_markers()
        return index


    def apply_file_changes(self, changes: list[tuple[str, Path]]) -> None:
        """Update only changed paths without rescanning the complete project."""
        by_path: dict[str, IndexedFile] = {}
        for item in self.files:
            key = _relative_path_key(item.relative_path)
            if key is not None:
                by_path[key] = item

        for kind, relative in changes:
            normalized_kind = str(kind).strip().casefold()
            if normalized_kind not in {"created", "modified", "deleted"}:
                continue
            normalized = _normalize_relative_path(relative)
            if normalized is None:
                continue
            relative_path = str(normalized)
            key = _relative_path_key(normalized)
            if key is None:
                continue
            if normalized_kind == "deleted":
                by_path.pop(key, None)
                continue

            absolute = (self.root / normalized).resolve()
            try:
                absolute.relative_to(self.root.resolve())
            except ValueError:
                continue
            try:
                if not absolute.is_file():
                    by_path.pop(key, None)
                    continue
                size = absolute.stat().st_size
            except OSError:
                continue
            suffix = absolute.suffix.lower() or "[uzantısız]"
            by_path[key] = IndexedFile(relative_path, suffix, size)

        self.files = sorted(by_path.values(), key=lambda item: item.relative_path.casefold())
        self.extension_counts = Counter(item.suffix for item in self.files)
        self.markers = self._detect_markers()


    def reconcile_snapshot(self, snapshot: dict[Path, tuple[int, int]]) -> int:
        """Repair index drift using the watcher snapshot without a full project rescan."""
        indexed: dict[str, IndexedFile] = {}
        for item in self.files:
            key = _relative_path_key(item.relative_path)
            if key is not None:
                indexed[key] = item

        snapshot_paths: dict[str, tuple[Path, tuple[int, int]]] = {}
        observed_snapshot_keys: set[str] = set()
        for path, state in snapshot.items():
            normalized = _normalize_relative_path(path)
            key = _relative_path_key(path)
            if normalized is None or key is None:
                continue
            observed_snapshot_keys.add(key)
            if not isinstance(state, (tuple, list)) or len(state) != 2:
                continue
            try:
                modified_ns = int(state[0])
                size = max(0, int(state[1]))
            except (TypeError, ValueError, OverflowError):
                continue
            snapshot_paths[key] = (normalized, (modified_ns, size))
        changes: list[tuple[str, Path]] = []

        for key in indexed.keys() - observed_snapshot_keys:
            changes.append(("deleted", Path(indexed[key].relative_path)))
        for key, (relative, (_, size)) in snapshot_paths.items():
            current = indexed.get(key)
            if current is None:
                changes.append(("created", relative))
            elif current.size != size or current.relative_path != str(relative):
                changes.append(("modified", relative))

        if changes:
            self.apply_file_changes(changes)
        return len(changes)

    def _detect_markers(self) -> list[str]:
        names = {Path(item.relative_path).name.lower() for item in self.files}
        markers: list[str] = []
        checks = [
            ("pyproject.toml", "Python / pyproject"),
            ("requirements.txt", "Python / pip"),
            ("package.json", "Node.js / JavaScript"),
            ("cargo.toml", "Rust / Cargo"),
            ("go.mod", "Go"),
            ("cmakelists.txt", "CMake"),
            ("meson.build", "Meson"),
            ("pom.xml", "Java / Maven"),
            ("build.gradle", "Java / Gradle"),
        ]
        for filename, label in checks:
            if filename in names:
                markers.append(label)
        if any(item.suffix == ".qml" for item in self.files):
            markers.append("Qt Quick / QML")
        if any(item.suffix in {".cpp", ".cc", ".cxx", ".h", ".hpp"} for item in self.files):
            markers.append("C/C++")
        if any(item.suffix == ".cs" for item in self.files):
            markers.append("C# / .NET")
        return list(dict.fromkeys(markers))

    def summary(self, top_extensions: int = 12) -> str:
        total_size = sum(item.size for item in self.files)
        extension_lines = [
            f"- {suffix}: {count} dosya"
            for suffix, count in self.extension_counts.most_common(top_extensions)
        ]
        tech = ", ".join(self.markers) if self.markers else "Belirgin teknoloji işareti bulunamadı"
        return (
            f"Proje: {self.root.name}\n"
            f"Dosya sayısı: {len(self.files)}\n"
            f"İndekslenen toplam boyut: {self._human_size(total_size)}\n"
            f"Algılanan teknolojiler: {tech}\n"
            "En sık uzantılar:\n" + ("\n".join(extension_lines) or "- Dosya yok")
        )

    def relevant_files(self, query: str, limit: int = 8) -> list[str]:
        if not isinstance(query, str):
            return []
        try:
            result_limit = max(0, int(limit))
        except (TypeError, ValueError, OverflowError):
            result_limit = 8
        if result_limit == 0:
            return []

        tokens = {
            token for token in re.findall(r"[a-zA-Z0-9_ğüşöçıİĞÜŞÖÇ]+", query.casefold())
            if len(token) >= 3
        }
        if not tokens:
            return []

        scored: list[tuple[int, str]] = []
        for item in self.files:
            lowered = item.relative_path.lower()
            score = sum(4 for token in tokens if token in lowered)
            if item.suffix in TEXT_EXTENSIONS:
                score += 1
            filename = Path(lowered).name
            if any(token == filename.split(".")[0] for token in tokens):
                score += 5
            if score > 0:
                scored.append((score, item.relative_path))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [path for _, path in scored[:result_limit]]

    @staticmethod
    def _human_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"
