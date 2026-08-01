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


def _line_churn(old: str, new: str) -> int:
    matcher = difflib.SequenceMatcher(
        None, old.splitlines(), new.splitlines(), autojunk=True
    )
    return sum(
        (old_end - old_start) + (new_end - new_start)
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes()
        if tag != "equal"
    )


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
        output_bytes = len(new.encode("utf-8"))
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
