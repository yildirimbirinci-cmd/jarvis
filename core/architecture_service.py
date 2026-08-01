from __future__ import annotations

import ast
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from artmach_assistant.core.project_index import IGNORED_DIRS, TEXT_EXTENSIONS
from artmach_assistant.core.workspace import WorkspaceService

_MAX_SCAN_FILES = 12_000
_MAX_SOURCE_CHARS = 2_000_000
_MAX_REPORT_EDGES = 10_000
_MAX_TEXT = 20_000


def _safe_text(value: Any, *, limit: int = _MAX_TEXT) -> str:
    try:
        text = str(value)
    except BaseException:
        return ""
    return text.replace("\x00", "")[:limit]


def _positive_int(value: Any, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(max(1, number), maximum)


@dataclass
class ProjectMap:
    folders: list[dict] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def report(self) -> str:
        lines = [
            "PROJE HARİTASI",
            f"Dosya: {self.totals.get('files', 0)} | Klasör: {self.totals.get('folders', 0)} | Sınıf: {self.totals.get('classes', 0)} | Fonksiyon: {self.totals.get('functions', 0)}",
            "",
        ]
        for row in self.folders[:_MAX_SCAN_FILES]:
            try:
                depth = _positive_int(row.get("depth", 0), default=1, maximum=100) if row.get("depth", 0) else 0
                name = _safe_text(row.get("name", ""), limit=512)
                files = max(0, int(row.get("files", 0)))
                classes = max(0, int(row.get("classes", 0)))
                functions = max(0, int(row.get("functions", 0)))
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
            lines.append(f"{'  ' * depth}{name}/  [{files} dosya, {classes} sınıf, {functions} fonksiyon]")
        return "\n".join(lines)


@dataclass
class DependencyGraph:
    outgoing: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    incoming: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(self, source: str, target: str) -> None:
        source_text = _safe_text(source, limit=2048).replace("\\", "/").strip()
        target_text = _safe_text(target, limit=2048).replace("\\", "/").strip()
        if not source_text or not target_text or source_text == target_text:
            return
        self.outgoing[source_text].add(target_text)
        self.incoming[target_text].add(source_text)

    def report(self, focus: str = "", limit: int = 180) -> str:
        focus_text = _safe_text(focus).replace("\\", "/").strip()
        edge_limit = _positive_int(limit, default=180, maximum=_MAX_REPORT_EDGES)
        if focus_text:
            candidates = [
                path for path in set(self.outgoing) | set(self.incoming)
                if focus_text.casefold() in path.casefold()
            ]
            if not candidates:
                return f"Bağımlılık kaydı bulunamadı: {focus_text}"
            node = sorted(candidates, key=lambda path: (len(path), path))[0]
            used = sorted(self.outgoing.get(node, set()))[:edge_limit]
            users = sorted(self.incoming.get(node, set()))[:edge_limit]
            return "\n".join([
                f"DOSYA: {node}", "", "KULLANDIĞI DOSYALAR:",
                *([f"  -> {item}" for item in used] or ["  (yok)"]),
                "", "BU DOSYAYI KULLANANLAR:",
                *([f"  <- {item}" for item in users] or ["  (yok)"]),
            ])
        edges = sorted(
            (source, target)
            for source, targets in self.outgoing.items()
            for target in targets
        )
        ranked = Counter(target for _, target in edges)
        lines = [
            f"BAĞIMLILIK GRAFİĞİ: {len(set(self.outgoing) | set(self.incoming))} düğüm, {len(edges)} bağlantı",
            "",
            "EN ÇOK KULLANILAN DOSYALAR:",
        ]
        lines.extend(f"- {name}: {count} bağlantı" for name, count in ranked.most_common(20))
        lines.append("\nBAĞLANTILAR:")
        lines.extend(f"{source} -> {target}" for source, target in edges[:edge_limit])
        if len(edges) > edge_limit:
            lines.append("… bağlantı listesi sınırlandırıldı")
        return "\n".join(lines)


class ArchitectureService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def project_map(self) -> ProjectMap:
        root = Path(self.workspace.require_root()).resolve(strict=False)
        stats: dict[Path, dict[str, int]] = defaultdict(
            lambda: {"files": 0, "classes": 0, "functions": 0}
        )
        total_classes = total_functions = total_files = 0
        dirs: set[Path] = {Path(".")}
        for path in self._iter_paths(root):
            if path.is_symlink() or self._is_ignored(root, path):
                continue
            try:
                relative = path.relative_to(root)
                is_dir = path.is_dir()
            except (OSError, ValueError):
                continue
            if is_dir:
                dirs.add(relative)
                continue
            total_files += 1
            classes, functions = self._symbol_counts(path)
            total_classes += classes
            total_functions += functions
            parent = relative.parent
            while True:
                dirs.add(parent)
                stats[parent]["files"] += 1
                stats[parent]["classes"] += classes
                stats[parent]["functions"] += functions
                if parent == Path("."):
                    break
                parent = parent.parent
        rows = []
        for folder in sorted(dirs, key=lambda item: (len(item.parts), str(item).casefold())):
            name = root.name if folder == Path(".") else folder.name
            rows.append({
                "path": str(folder),
                "name": name,
                "depth": 0 if folder == Path(".") else len(folder.parts),
                **stats[folder],
            })
        return ProjectMap(rows, {
            "files": total_files,
            "folders": len(dirs),
            "classes": total_classes,
            "functions": total_functions,
        })

    def dependency_graph(self) -> DependencyGraph:
        root = Path(self.workspace.require_root()).resolve(strict=False)
        files: list[Path] = []
        for path in self._iter_paths(root):
            try:
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.suffix.casefold() not in TEXT_EXTENSIONS
                    or self._is_ignored(root, path)
                ):
                    continue
            except OSError:
                continue
            files.append(path)
        rels = {path.relative_to(root).as_posix(): path for path in files}
        stem_index: dict[str, list[str]] = defaultdict(list)
        for relative in rels:
            stem_index[Path(relative).stem.casefold()].append(relative)
        graph = DependencyGraph()
        for relative, path in sorted(rels.items()):
            text = self._read_limited(path)
            if text is None:
                continue
            for reference in self._references(path.suffix.casefold(), text):
                target = self._resolve_reference(relative, reference, rels, stem_index)
                if target:
                    graph.add(relative, target)
        return graph

    @staticmethod
    def _iter_paths(root: Path):
        count = 0
        try:
            iterator = root.rglob("*")
            for path in iterator:
                count += 1
                if count > _MAX_SCAN_FILES:
                    break
                yield path
        except OSError:
            return

    @staticmethod
    def _read_limited(path: Path) -> str | None:
        try:
            if path.is_symlink() or path.stat().st_size > _MAX_SOURCE_CHARS * 4:
                return None
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            return None
        return text if len(text) <= _MAX_SOURCE_CHARS else None

    @staticmethod
    def _is_ignored(root: Path, path: Path) -> bool:
        try:
            relative = path.relative_to(root)
            parts = relative.parts if path.is_dir() else relative.parts[:-1]
        except (OSError, ValueError):
            return True
        return any(part in IGNORED_DIRS for part in parts)

    @classmethod
    def _symbol_counts(cls, path: Path) -> tuple[int, int]:
        text = cls._read_limited(path)
        if text is None:
            return 0, 0
        if path.suffix.casefold() == ".py":
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError, MemoryError, RecursionError):
                return 0, 0
            return (
                sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree)),
                sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)),
            )
        if path.suffix.casefold() in {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".cs", ".java", ".kt"}:
            classes = len(re.findall(r"\b(?:class|struct|interface)\s+[A-Za-z_]\w*", text))
            functions = len(re.findall(
                r"^[\t ]*(?:[\w:<>,~*&]+[\t ]+)+[A-Za-z_]\w*\s*\([^;{}]*\)\s*(?:const\s*)?\{",
                text,
                re.MULTILINE,
            ))
            return classes, functions
        return 0, 0

    @staticmethod
    def _references(suffix: str, text: str) -> list[str]:
        if len(text) > _MAX_SOURCE_CHARS:
            return []
        if suffix == ".py":
            refs: list[str] = []
            try:
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        refs.extend(alias.name.replace(".", "/") for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        base = (node.module or "").replace(".", "/")
                        if base:
                            refs.append(base)
                        refs.extend(
                            f"{base}/{alias.name}".strip("/")
                            for alias in node.names
                            if alias.name != "*"
                        )
                return refs[:_MAX_SCAN_FILES]
            except (SyntaxError, ValueError, MemoryError, RecursionError):
                refs = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", text, re.MULTILINE)
                return [item.replace(".", "/") for item in refs[:_MAX_SCAN_FILES]]
        if suffix in {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp"}:
            return re.findall(r"#\s*include\s*[\"<]([^\">]+)[\">]", text)[:_MAX_SCAN_FILES]
        if suffix in {".js", ".ts", ".tsx", ".jsx", ".qml"}:
            return re.findall(r"(?:from\s+|require\s*\(|import\s+)[\"']([^\"']+)", text)[:_MAX_SCAN_FILES]
        return []

    @staticmethod
    def _resolve_reference(source: str, ref: str, rels: dict[str, Path], stems: dict[str, list[str]]) -> str | None:
        reference = _safe_text(ref, limit=2048).replace("\\", "/").strip("./")
        if not reference or "\x00" in reference:
            return None
        base = Path(source).parent
        candidates = [(base / reference).as_posix(), reference]
        for candidate in list(candidates):
            candidates.extend(candidate + extension for extension in (".py", ".qml", ".js", ".ts", ".h", ".hpp", ".cpp"))
            candidates.extend((Path(candidate) / name).as_posix() for name in ("__init__.py", "index.js", "index.ts"))
        for candidate in candidates[:100]:
            if candidate in rels:
                return candidate
        matches = stems.get(Path(reference).stem.casefold(), [])
        return sorted(matches)[0] if len(matches) == 1 else None
