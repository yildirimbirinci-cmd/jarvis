from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TrustApprovalItem:
    path: str
    recommendation: str
    headline: str
    short_summary: str
    voice_summary: str
    decision_guidance: str
    changed_files: tuple[str, ...]
    warnings: tuple[str, ...]
    modified_at: float


class TrustApprovalInbox:
    """Read-only index of explainable approval presentations.

    This service never approves, rejects, commits or pushes. It only presents
    persisted trust reports so UI and voice flows cannot bypass approval gates.
    """

    def __init__(self, roots: Iterable[str | Path]) -> None:
        self.roots = tuple(Path(root).expanduser().resolve() for root in roots)

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError("approval presentation is unavailable")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("approval presentation must be an object")
        return payload

    @staticmethod
    def _strings(value: object, limit: int = 20) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(str(item).strip() for item in value if str(item).strip())[:limit]

    def list_items(self) -> tuple[TrustApprovalItem, ...]:
        found: dict[Path, TrustApprovalItem] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for path in root.rglob("approval_trust_presentation.json"):
                try:
                    payload = self._read(path)
                    item = TrustApprovalItem(
                        path=str(path),
                        recommendation=str(payload.get("recommendation", "hold")),
                        headline=str(payload.get("headline", "Onay raporu")),
                        short_summary=str(payload.get("short_summary", "")),
                        voice_summary=str(payload.get("voice_summary", "")),
                        decision_guidance=str(payload.get("decision_guidance", "")),
                        changed_files=self._strings(payload.get("change_lines")),
                        warnings=self._strings(payload.get("warning_lines")),
                        modified_at=path.stat().st_mtime,
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    continue
                found[path.resolve()] = item
        return tuple(sorted(found.values(), key=lambda row: row.modified_at, reverse=True))

    def latest(self) -> TrustApprovalItem | None:
        items = self.list_items()
        return items[0] if items else None

    def render_text(self, *, limit: int = 10) -> str:
        items = self.list_items()[: max(1, limit)]
        if not items:
            return "Bekleyen veya kaydedilmiş güven raporu bulunamadı."
        rows = [f"Onay raporları: {len(items)}"]
        for index, item in enumerate(items, 1):
            rows.append(f"\n{index}. {item.headline}")
            if item.short_summary:
                rows.append(item.short_summary)
            if item.changed_files:
                rows.append("Dosyalar: " + ", ".join(item.changed_files))
            if item.warnings:
                rows.append("Uyarılar: " + "; ".join(item.warnings))
            rows.append("Yönlendirme: " + item.decision_guidance)
        return "\n".join(rows)
