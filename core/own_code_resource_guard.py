"""Bound the size and target types of model-generated own-code patches."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import PurePosixPath


MAX_TOTAL_OUTPUT_BYTES = 1_500_000
MAX_FILE_OUTPUT_BYTES = 500_000
MAX_CHANGED_LINES = 4_000
_PROTECTED_PARTS = {".git", ".venv", "venv", "__pycache__", ".secrets"}
_PROTECTED_NAMES = {".env", ".env.local", ".env.production"}
_BINARY_SUFFIXES = {
    ".exe", ".dll", ".so", ".dylib", ".pyd", ".bin", ".zip", ".7z",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".wav", ".mp3",
    ".mp4", ".pdf", ".db", ".sqlite", ".sqlite3", ".pyc",
}


@dataclass(frozen=True, slots=True)
class ResourceGuardResult:
    valid: bool
    output_bytes: int
    changed_lines: int
    issues: tuple[str, ...]

    def report(self) -> str:
        scope = (
            f"taslak boyutu {self.output_bytes} bayt, "
            f"yaklaşık {self.changed_lines} değişen satır"
        )
        if self.valid:
            return "Kaynak bütçesi uygun: " + scope + "."
        return "Kaynak bütçesi ihlali: " + "; ".join(self.issues) + f" ({scope})."


def _changed_span_metrics(old: str, new: str) -> tuple[int, int]:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    prefix = 0
    prefix_limit = min(len(old_lines), len(new_lines))
    while prefix < prefix_limit and old_lines[prefix] == new_lines[prefix]:
        prefix += 1
    old_suffix = len(old_lines)
    new_suffix = len(new_lines)
    while (
        old_suffix > prefix
        and new_suffix > prefix
        and old_lines[old_suffix - 1] == new_lines[new_suffix - 1]
    ):
        old_suffix -= 1
        new_suffix -= 1
    old_changed = old_lines[prefix:old_suffix]
    new_changed = new_lines[prefix:new_suffix]
    changed_lines = len(old_changed) + len(new_changed)
    changed_output_bytes = len("".join(new_changed).encode("utf-8"))
    return changed_lines, changed_output_bytes

def _line_churn(old: str, new: str) -> int:
    return _changed_span_metrics(old, new)[0]

def _changed_output_bytes(old: str, new: str) -> int:
    return _changed_span_metrics(old, new)[1]


def validate_resource_budget(changes: object) -> ResourceGuardResult:
    total_bytes = 0
    changed_lines = 0
    issues: list[str] = []
    for change in tuple(changes or ()):
        raw_path = str(getattr(change, "path", "")).replace("\\", "/")
        path = PurePosixPath(raw_path)
        lowered_parts = {part.casefold() for part in path.parts}
        name = path.name.casefold()
        suffix = path.suffix.casefold()
        new = str(getattr(change, "new_content", "") or "")
        old = str(getattr(change, "old_content", "") or "")
        full_output_bytes = len(new.encode("utf-8"))
        output_bytes = (
            _changed_output_bytes(old, new)
            if old
            else full_output_bytes
        )
        total_bytes += output_bytes
        changed_lines += _line_churn(old, new)
        if lowered_parts.intersection(_PROTECTED_PARTS) or name in _PROTECTED_NAMES:
            issues.append(f"korunan çalışma alanı hedefi: {raw_path}")
        if suffix in _BINARY_SUFFIXES:
            issues.append(f"metin patch'iyle değiştirilemeyen dosya türü: {raw_path}")
        if output_bytes > MAX_FILE_OUTPUT_BYTES:
            issues.append(f"tek dosya boyut sınırı aşılıyor: {raw_path}")
    if total_bytes > MAX_TOTAL_OUTPUT_BYTES:
        issues.append("toplam taslak boyut sınırı aşılıyor")
    if changed_lines > MAX_CHANGED_LINES:
        issues.append("değişen satır bütçesi aşılıyor")
    return ResourceGuardResult(
        not issues, total_bytes, changed_lines, tuple(issues)
    )
