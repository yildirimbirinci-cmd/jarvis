from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .engineering_brain import EngineeringPlanStore

_SCHEMA_VERSION = 1
_MAX_BYTES = 8 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_object(path: Path, *, required: bool = False) -> dict[str, object]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return {}
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError(f"report input is oversized: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"report input is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"report input must be an object: {path}")
    return payload


def _atomic_write(path: Path, payload: object) -> Path:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        raise ValueError("engineering morning report is oversized")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _audit_rows(path: Path) -> tuple[list[dict[str, object]], bool]:
    if not path.is_file():
        return [], True
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("delegated audit is oversized")
    rows: list[dict[str, object]] = []
    expected_previous = "0" * 64
    integrity = True
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("delegated audit is invalid") from exc
        if not isinstance(row, dict):
            raise ValueError("delegated audit row must be an object")
        supplied_hash = str(row.get("record_hash", ""))
        previous_hash = str(row.get("previous_hash", ""))
        canonical_row = dict(row)
        canonical_row.pop("record_hash", None)
        canonical = json.dumps(canonical_row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        calculated = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if previous_hash != expected_previous or supplied_hash != calculated:
            integrity = False
        expected_previous = supplied_hash or expected_previous
        rows.append(row)
    return rows, integrity


def _safe_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


@dataclass(frozen=True, slots=True)
class EngineeringMorningSummary:
    schema_version: int
    plan_id: str
    request: str
    domain: str
    plan_status: str
    progress_percent: int
    completed_steps: int
    total_steps: int
    blocked_steps: int
    failed_steps: int
    stalled_steps: tuple[str, ...]
    automatic_commits: int
    waiting_owner: int
    push_performed: bool
    audit_integrity_ok: bool
    recommendation: str
    generated_at: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["stalled_steps"] = list(self.stalled_steps)
        return payload


class EngineeringMorningReport:
    """Build one owner-facing report from a long-running engineering plan.

    The report is read-only. It never approves, commits, pushes, changes a plan,
    or consumes an approval token. It combines durable plan/progress state with
    the delegated approval audit and keeps an integrity signal for that audit.
    """

    def __init__(
        self,
        engineering_plan_path: str | Path,
        *,
        progress_path: str | Path | None = None,
        delegated_audit_path: str | Path | None = None,
        clock=_now,
    ) -> None:
        self.plan_path = Path(engineering_plan_path).expanduser().resolve(strict=False)
        self.progress_path = (
            Path(progress_path).expanduser().resolve(strict=False)
            if progress_path is not None
            else self.plan_path.with_name(f"{self.plan_path.stem}.progress.json")
        )
        self.audit_path = (
            Path(delegated_audit_path).expanduser().resolve(strict=False)
            if delegated_audit_path is not None
            else None
        )
        self.clock = clock

    def _snapshot(self) -> tuple[EngineeringMorningSummary, dict[str, object]]:
        plan = EngineeringPlanStore(self.plan_path).load()
        progress_payload = _read_object(self.progress_path)
        snapshot_raw = progress_payload.get("snapshot")
        snapshot = dict(snapshot_raw) if isinstance(snapshot_raw, Mapping) else {}
        states = [step.status for step in plan.steps]
        total = int(snapshot.get("total_steps", len(states)))
        completed = int(snapshot.get("completed_steps", states.count("completed")))
        blocked = int(snapshot.get("blocked_steps", states.count("blocked")))
        failed = int(snapshot.get("failed_steps", states.count("failed")))
        progress = int(snapshot.get("progress_percent", round(100 * completed / total) if total else 0))
        recommendation = str(snapshot.get("recommendation", "complete" if plan.status == "completed" else "continue"))
        stalled = _safe_strings(snapshot.get("stalled_step_ids"))
        audit_rows, integrity = _audit_rows(self.audit_path) if self.audit_path is not None else ([], True)
        committed = sum(1 for row in audit_rows if row.get("status") == "committed")
        waiting = sum(1 for row in audit_rows if row.get("status") == "waiting_owner")
        push = any(bool(row.get("push_performed")) for row in audit_rows)
        summary = EngineeringMorningSummary(
            _SCHEMA_VERSION,
            plan.plan_id,
            plan.request,
            plan.domain,
            plan.status,
            max(0, min(100, progress)),
            completed,
            total,
            blocked,
            failed,
            stalled,
            committed,
            waiting,
            push,
            integrity,
            recommendation,
            self.clock().astimezone(timezone.utc).isoformat(),
        )
        details = {
            "steps": [step.to_dict() for step in plan.steps],
            "delegated_decisions": audit_rows,
            "progress_snapshot": snapshot,
        }
        return summary, details

    @staticmethod
    def _turkish_text(summary: EngineeringMorningSummary) -> str:
        lines = [
            "Gece Mühendislik Raporu",
            "========================",
            f"Görev: {summary.request}",
            f"Alan: {summary.domain}",
            f"Plan durumu: {summary.plan_status}",
            f"İlerleme: %{summary.progress_percent} ({summary.completed_steps}/{summary.total_steps} adım)",
            f"Engellenen adım: {summary.blocked_steps}",
            f"Başarısız adım: {summary.failed_steps}",
            f"Otomatik commit: {summary.automatic_commits}",
            f"Kullanıcı onayı bekleyen: {summary.waiting_owner}",
            f"Push yapıldı: {'evet' if summary.push_performed else 'hayır'}",
            f"Audit bütünlüğü: {'doğrulandı' if summary.audit_integrity_ok else 'BOZUK'}",
            f"Öneri: {summary.recommendation}",
        ]
        if summary.stalled_steps:
            lines.append("Takılmış adımlar: " + ", ".join(summary.stalled_steps))
        if not summary.audit_integrity_ok:
            lines.append("UYARI: Delegated approval audit zinciri doğrulanamadı; otomatik sonuçlara güvenme.")
        if summary.push_performed:
            lines.append("UYARI: Gece politikası sırasında push kaydı bulundu; owner incelemesi gerekli.")
        return "\n".join(lines) + "\n"

    def build(self, output_path: str | Path, *, text_output_path: str | Path | None = None) -> Path:
        summary, details = self._snapshot()
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "summary": summary.to_dict(),
            **details,
        }
        target = Path(output_path).expanduser().resolve(strict=False)
        _atomic_write(target, payload)
        if text_output_path is not None:
            text_target = Path(text_output_path).expanduser().resolve(strict=False)
            text_target.parent.mkdir(parents=True, exist_ok=True)
            text_target.write_text(self._turkish_text(summary), encoding="utf-8", newline="\n")
        return target
