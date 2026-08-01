"""Bounded call-graph context selection for safe patch proposals."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b")
_STOP_WORDS = {
    "bir", "bu", "ve", "veya", "ile", "icin", "için", "olan", "olarak",
    "dosya", "dosyayi", "dosyayı", "kod", "kodu", "degisiklik", "değişiklik",
    "ekle", "duzelt", "düzelt", "yap", "hazirla", "hazırla", "the", "and",
    "for", "from", "into", "with", "change", "update", "fix", "add", "remove",
}


class _DependencyIndex(Protocol):
    def symbol_impact(self, name: str, *, limit: int = 2000): ...
    def call_graph_caller_paths(
        self, canonical_name: str, *, max_depth: int = 5, max_paths: int = 1000
    ): ...
    def call_graph_callee_paths(
        self, canonical_name: str, *, max_depth: int = 5, max_paths: int = 1000
    ): ...


@dataclass(frozen=True, slots=True)
class PatchContextFile:
    path: str
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatchContextResult:
    query: str
    symbols: tuple[str, ...]
    files: tuple[PatchContextFile, ...]
    text: str
    used_call_graph: bool = False


class CallGraphPatchContextBuilder:
    """Select the smallest useful source context without executing project code."""

    def __init__(
        self,
        project_root: str | Path,
        dependency_index: _DependencyIndex,
        read_text: Callable[[str, int], str],
    ) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._dependency_index = dependency_index
        self._read_text = read_text

    def build(
        self,
        query: str,
        *,
        max_symbols: int = 8,
        max_files: int = 8,
        max_chars_each: int = 7000,
        max_total_chars: int = 40_000,
        max_depth: int = 3,
    ) -> PatchContextResult:
        normalized_query = self._safe_text(query)[:20_000].replace("\x00", "")
        bounded_chars = self._bounded_int(
            max_chars_each, default=7000, minimum=256, maximum=100_000
        )
        bounded_total_chars = self._bounded_int(
            max_total_chars, default=40_000, minimum=256, maximum=1_000_000
        )
        bounded_depth = self._bounded_int(
            max_depth, default=3, minimum=1, maximum=10
        )
        symbols = self._candidate_symbols(normalized_query, max_symbols=max_symbols)
        evidence: dict[str, dict[str, object]] = {}
        chain_rows: list[str] = []
        used_call_graph = False
        for symbol in symbols:
            canonical_names: list[str] = [symbol] if "." in symbol else []
            try:
                impact = self._dependency_index.symbol_impact(symbol, limit=1000)
            except Exception:
                impact = None
            definitions = self._safe_items(
                getattr(impact, "definitions", ()) if impact is not None else ()
            )
            for definition in definitions:
                path = self._relative_path(getattr(definition, "path", ""))
                if path:
                    self._add_evidence(evidence, path, 100, f"{symbol} tanımı")
                canonical = self._canonical_name(definition)
                if canonical:
                    canonical_names.append(canonical)
            files = self._safe_items(
                getattr(impact, "files", ()) if impact is not None else ()
            )
            for item in files:
                path = self._relative_path(getattr(item, "path", ""))
                if not path:
                    continue
                weight = self._bounded_int(
                    getattr(item, "weight", 1), default=1, minimum=1, maximum=10_000
                )
                self._add_evidence(evidence, path, min(80, 20 + weight), f"{symbol} etkisi")
                for edge in self._safe_items(getattr(item, "call_edges", ())):
                    caller = self._relative_path(getattr(edge, "caller_path", ""))
                    callee = self._relative_path(getattr(edge, "callee_path", ""))
                    if caller:
                        self._add_evidence(evidence, caller, 70, f"{symbol} çağıranı")
                    if callee:
                        self._add_evidence(evidence, callee, 55, f"{symbol} çağrı hedefi")
                    canonical = self._safe_text(
                        getattr(edge, "callee_canonical_name", "")
                    )
                    if canonical:
                        canonical_names.append(canonical)
            canonical_candidates = self._unique_text(canonical_names, limit=4)
            for canonical in canonical_candidates:
                for direction, method in (
                    ("çağıran", self._dependency_index.call_graph_caller_paths),
                    ("çağrılan", self._dependency_index.call_graph_callee_paths),
                ):
                    try:
                        traversal = method(canonical, max_depth=bounded_depth, max_paths=80)
                    except Exception:
                        continue
                    paths = self._safe_items(getattr(traversal, "paths", ()))
                    if paths:
                        used_call_graph = True
                    for path_result in paths:
                        symbols_in_path = self._unique_text(
                            getattr(path_result, "symbols", ()), limit=100
                        )
                        if symbols_in_path:
                            suffix = " [döngü]" if getattr(path_result, "is_cycle", False) else ""
                            chain_rows.append(
                                f"- {direction}: {' -> '.join(symbols_in_path)}{suffix}"
                            )
                        for edge in self._safe_items(getattr(path_result, "edges", ())):
                            caller = self._relative_path(getattr(edge, "caller_path", ""))
                            callee = self._relative_path(getattr(edge, "callee_path", ""))
                            if caller:
                                self._add_evidence(evidence, caller, 45, f"{canonical} {direction} zinciri")
                            if callee:
                                self._add_evidence(evidence, callee, 40, f"{canonical} {direction} zinciri")
        ordered = sorted(
            (
                PatchContextFile(
                    path=str(row["path"]),
                    score=int(row["score"]),
                    reasons=tuple(sorted(row["reasons"])),
                )
                for row in evidence.values()
            ),
            key=lambda item: (-item.score, item.path.casefold()),
        )[: self._bounded_int(max_files, default=8, minimum=1, maximum=20)]
        chunks: list[str] = []
        included_files: list[PatchContextFile] = []
        remaining_chars = bounded_total_chars
        if chain_rows and remaining_chars > 0:
            unique_rows = self._unique_text(chain_rows, limit=40)
            summary = "ÇAĞRI GRAFİĞİ ÖZETİ:\n" + "\n".join(unique_rows)
            summary = summary[:remaining_chars]
            if summary:
                chunks.append(summary)
                remaining_chars -= len(summary)
        for item in ordered:
            if remaining_chars <= 0:
                break
            reasons = ", ".join(item.reasons)
            header = (
                f"\n--- DOSYA: {item.path} | PUAN: {item.score} | "
                f"NEDEN: {reasons} ---\n"
            )
            read_limit = min(bounded_chars, remaining_chars)
            try:
                content = self._read_text(item.path, read_limit)
            except (OSError, RuntimeError, TypeError, ValueError, UnicodeError):
                continue
            if not isinstance(content, str):
                continue
            content = content[:read_limit]
            if not content:
                continue
            chunks.append(header + content)
            included_files.append(item)
            remaining_chars -= len(content)
        rendered = "\n".join(chunks)
        if chain_rows:
            rendered = rendered[:bounded_total_chars]
        return PatchContextResult(
            query=normalized_query,
            symbols=symbols,
            files=tuple(included_files),
            text=rendered,
            used_call_graph=used_call_graph,
        )

    @staticmethod
    def _candidate_symbols(query: str, *, max_symbols: int) -> tuple[str, ...]:
        values: list[str] = []
        seen: set[str] = set()
        for token in _IDENTIFIER_RE.findall(CallGraphPatchContextBuilder._safe_text(query)[:20_000].replace("\x00", "")):
            key = token.casefold()
            if key in _STOP_WORDS or len(token) < 3 or key in seen:
                continue
            seen.add(key)
            values.append(token)
        values.sort(key=lambda value: ("." not in value, -len(value), value.casefold()))
        limit = CallGraphPatchContextBuilder._bounded_int(
            max_symbols, default=8, minimum=1, maximum=20
        )
        return tuple(values[:limit])

    def _canonical_name(self, definition: object) -> str:
        path = self._relative_path(getattr(definition, "path", ""))
        qualified = self._safe_text(getattr(definition, "qualified_name", ""))
        if not path or not qualified:
            return ""
        parts = list(Path(path).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        module = ".".join(parts)
        return f"{module}.{qualified}" if module else qualified

    def _relative_path(self, value: object) -> str:
        if not value:
            return ""
        try:
            path = Path(str(value)).expanduser()
            absolute = (
                path.resolve(strict=False)
                if path.is_absolute()
                else (self.root / path).resolve(strict=False)
            )
            relative = absolute.relative_to(self.root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return ""
        if any(part in {".git", "venv", ".venv", "__pycache__"} for part in relative.parts):
            return ""
        return relative.as_posix()

    @staticmethod
    def _safe_items(value: object) -> tuple[object, ...]:
        """Materialize malformed/stale iterables without losing valid prefix items."""
        if value is None or isinstance(value, (str, bytes, bytearray)):
            return ()
        try:
            iterator = iter(value)  # type: ignore[arg-type]
        except (TypeError, RuntimeError, ValueError):
            return ()
        items: list[object] = []
        while True:
            try:
                items.append(next(iterator))
            except StopIteration:
                break
            except (TypeError, RuntimeError, ValueError, OverflowError, MemoryError, RecursionError):
                break
        return tuple(items)

    @staticmethod
    def _safe_text(value: object) -> str:
        try:
            return str(value).strip()
        except (TypeError, RuntimeError, ValueError, UnicodeError, RecursionError):
            return ""

    @staticmethod
    def _unique_text(values: object, *, limit: int) -> tuple[str, ...]:
        """Return non-empty strings once, preserving order case-insensitively."""
        unique: list[str] = []
        seen: set[str] = set()
        for value in CallGraphPatchContextBuilder._safe_items(values):
            text = CallGraphPatchContextBuilder._safe_text(value)
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(text)
            if len(unique) >= limit:
                break
        return tuple(unique)

    @staticmethod
    def _bounded_int(
        value: object, *, default: int, minimum: int, maximum: int
    ) -> int:
        if isinstance(value, bool):
            number = default
        else:
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                number = default
        return max(minimum, min(number, maximum))

    @staticmethod
    def _add_evidence(
        evidence: dict[str, dict[str, object]], path: str, score: int, reason: str
    ) -> None:
        normalized_path = path.replace("\\", "/").strip("/")
        if not normalized_path:
            return
        key = normalized_path.casefold()
        row = evidence.setdefault(
            key, {"path": normalized_path, "score": 0, "reasons": set()}
        )
        reasons = row["reasons"]
        if not isinstance(reasons, set) or reason in reasons:
            return
        reasons.add(reason)
        try:
            bounded_score = max(0, min(int(score), 10_000))
        except (TypeError, ValueError, OverflowError):
            bounded_score = 0
        row["score"] = min(1_000_000, int(row["score"]) + bounded_score)
