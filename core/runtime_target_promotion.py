from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from artmach_assistant.core.runtime_observability import RuntimeFinding
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

_SCHEMA_VERSION = 1
_MAX_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class RuntimeTargetOverride:
    finding_id: str
    source_path: str
    symbol: str
    source_fingerprint: str
    evidence_last_seen: str
    identity_samples: int

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "source_path": self.source_path,
            "symbol": self.symbol,
            "source_fingerprint": self.source_fingerprint,
            "evidence_last_seen": self.evidence_last_seen,
            "identity_samples": self.identity_samples,
        }

    @classmethod
    def from_dict(cls, row: dict[str, object]) -> "RuntimeTargetOverride":
        finding_id = str(row.get("finding_id", "") or "").strip().upper()
        source_path = str(row.get("source_path", "") or "").strip().replace("\\", "/")
        symbol = str(row.get("symbol", "") or "").strip()
        source_fingerprint = str(row.get("source_fingerprint", "") or "").strip()
        evidence_last_seen = str(row.get("evidence_last_seen", "") or "").strip()
        identity_samples = max(0, int(row.get("identity_samples", 0) or 0))
        if not finding_id or not source_path or not symbol or not source_fingerprint:
            raise ValueError("Incomplete runtime target override.")
        if source_path.startswith("/") or ".." in Path(source_path).parts:
            raise ValueError("Unsafe runtime target path.")
        return cls(
            finding_id=finding_id,
            source_path=source_path,
            symbol=symbol,
            source_fingerprint=source_fingerprint,
            evidence_last_seen=evidence_last_seen,
            identity_samples=identity_samples,
        )


class RuntimeTargetOverrideStore:
    """Small restart-safe store for evidence-proven runtime target promotion."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)

    def _load_all(self) -> dict[str, RuntimeTargetOverride]:
        if not self.path.exists():
            return {}
        try:
            payload = read_json_object(self.path, max_bytes=_MAX_BYTES)
            if payload.get("schema_version") != _SCHEMA_VERSION:
                return {}
            rows = payload.get("overrides", [])
            if not isinstance(rows, list):
                return {}
            result: dict[str, RuntimeTargetOverride] = {}
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                try:
                    item = RuntimeTargetOverride.from_dict(raw)
                except (TypeError, ValueError):
                    continue
                result[item.finding_id] = item
            return result
        except (OSError, UnicodeError, ValueError, TypeError):
            return {}

    def get(self, finding_id: str) -> RuntimeTargetOverride | None:
        return self._load_all().get(str(finding_id or "").strip().upper())

    def save(self, override: RuntimeTargetOverride) -> None:
        rows = self._load_all()
        rows[override.finding_id] = override
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "overrides": [
                rows[key].to_dict()
                for key in sorted(rows)
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, payload, max_bytes=_MAX_BYTES)

    def discard(self, finding_id: str) -> None:
        key = str(finding_id or "").strip().upper()
        rows = self._load_all()
        if key not in rows:
            return
        rows.pop(key, None)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "overrides": [
                rows[item].to_dict()
                for item in sorted(rows)
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, payload, max_bytes=_MAX_BYTES)


def build_target_override(
    finding: RuntimeFinding,
    report: object,
    *,
    source_fingerprint: str,
) -> RuntimeTargetOverride | None:
    if not bool(getattr(report, "locally_confirmed", False)):
        return None

    category = str(getattr(finding, "category", "") or "").strip()
    promotable_categories = {
        "repeated_slow_operation",
        "repeated_type_error",
        "repeated_attribute_error",
        "repeated_name_error",
        "repeated_import_error",
        "repeated_runtime_contract_error",
    }
    if category not in promotable_categories:
        return None

    action_ms = float(getattr(report, "action_median_ms", 0.0) or 0.0)
    wrapper_ms = float(getattr(report, "wrapper_median_ms", 0.0) or 0.0)
    timing_evidence_present = action_ms > 0.0 or wrapper_ms > 0.0
    if timing_evidence_present and action_ms <= max(wrapper_ms * 3.0, wrapper_ms + 1.0):
        return None

    source_path = str(getattr(report, "action_target_path", "") or "").strip().replace("\\", "/")
    symbol = str(getattr(report, "action_target_symbol", "") or "").strip()
    identity_samples = max(0, int(getattr(report, "action_identity_samples", 0) or 0))
    if not source_path or not symbol or identity_samples < 1:
        return None

    fingerprint = str(source_fingerprint or "").strip()
    if not fingerprint:
        return None

    return RuntimeTargetOverride(
        finding_id=str(finding.finding_id).strip().upper(),
        source_path=source_path,
        symbol=symbol,
        source_fingerprint=fingerprint,
        evidence_last_seen=str(getattr(finding, "last_seen", "") or ""),
        identity_samples=identity_samples,
    )


def apply_target_override(
    finding: RuntimeFinding,
    override: RuntimeTargetOverride | None,
    *,
    current_source_fingerprint: str,
) -> RuntimeFinding:
    if override is None:
        return finding
    if override.finding_id != str(finding.finding_id).strip().upper():
        return finding
    if override.source_fingerprint != str(current_source_fingerprint or "").strip():
        return finding
    explanation = (
        f"{finding.explanation} Yerel runtime dogrulamasi "
        f"{override.source_path} - {override.symbol} hedefini dogruladi."
    )
    recommendation = (
        "Yalnizca yerel runtime kanitinin gosterdigi gercek hedefi incele; "
        "kaynak ve test sozlesmesini dogrula, kok nedeni kanitla ve ancak sonra "
        "en kucuk davranis-koruyan duzeltmeyi mevcut validator/worktree zincirinde hazirla."
    )
    return replace(
        finding,
        affected_paths=(override.source_path,),
        affected_symbols=(override.symbol,),
        explanation=explanation,
        recommendation=recommendation,
    )
