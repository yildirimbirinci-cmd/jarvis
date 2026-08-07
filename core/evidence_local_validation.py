from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Iterable

from artmach_assistant.core.runtime_observability import RuntimeEvent, RuntimeFinding


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if n < 0 or n != n or n in (float("inf"), float("-inf")):
        return None
    return n


@dataclass(frozen=True, slots=True)
class LocalRuntimeValidationReport:
    finding_id: str
    target_path: str
    target_symbol: str
    sample_count: int
    action_median_ms: float
    wrapper_median_ms: float
    total_median_ms: float
    conclusion: str
    locally_confirmed: bool
    last_seen: str
    action_target_path: str = ""
    action_target_symbol: str = ""
    action_identity_samples: int = 0

    def report(self) -> str:
        status = "CONFIRMED" if self.locally_confirmed else "INSUFFICIENT"
        return "\n".join([
            "YEREL RUNTIME DOGRULAMA",
            "",
            f"Durum: {status}",
            f"Bulgu: {self.finding_id}",
            f"Konum: {self.target_path} - {self.target_symbol}",
            f"Ornek sayisi: {self.sample_count}",
            f"action_duration_ms ortanca: {self.action_median_ms:.3f}",
            f"wrapper_overhead_ms ortanca: {self.wrapper_median_ms:.3f}",
            f"total_duration_ms ortanca: {self.total_median_ms:.3f}",
            f"Son olay: {self.last_seen or '(yok)'}",
            *(
                [
                    f"Action hedefi: {self.action_target_path or '(bilinmiyor)'}"
                    f" - {self.action_target_symbol or '(bilinmiyor)'}",
                    f"Action kimlikli ornek: {self.action_identity_samples}",
                ]
                if self.action_target_path or self.action_target_symbol
                else []
            ),
            "",
            f"Sonuc: {self.conclusion}",
            "",
            "Kaynak kodu degistirilmedi.",
            "Patch taslagi uretilmedi.",
        ])


def build_local_runtime_validation(
    finding: RuntimeFinding,
    events: Iterable[RuntimeEvent],
    *,
    fallback_path: str = "",
    fallback_symbol: str = "",
    minimum_samples: int = 3,
) -> LocalRuntimeValidationReport:
    path = next(
        (str(v).strip().replace("\\", "/") for v in finding.affected_paths if str(v).strip()),
        str(fallback_path or "").strip().replace("\\", "/"),
    )
    symbol = next(
        (str(v).strip() for v in finding.affected_symbols if str(v).strip()),
        str(fallback_symbol or "").strip(),
    )
    rows = []
    for event in events:
        if path and event.source_path.replace("\\", "/") != path:
            continue
        if symbol and event.symbol != symbol:
            continue
        md = event.metadata or {}
        action = _num(md.get("action_duration_ms"))
        wrapper = _num(md.get("wrapper_overhead_ms"))
        total = _num(event.duration_ms)
        if action is None or wrapper is None or total is None:
            continue
        if not bool(md.get("action_started", True)):
            continue
        rows.append((event, action, wrapper, total))

    actions = [r[1] for r in rows]
    wrappers = [r[2] for r in rows]
    totals = [r[3] for r in rows]
    identities = Counter(
        (
            str((r[0].metadata or {}).get("action_path", "") or "").strip(),
            str((r[0].metadata or {}).get("action_symbol", "") or "").strip(),
        )
        for r in rows
        if str((r[0].metadata or {}).get("action_path", "") or "").strip()
        or str((r[0].metadata or {}).get("action_symbol", "") or "").strip()
    )
    if identities:
        (action_target_path, action_target_symbol), action_identity_samples = identities.most_common(1)[0]
    else:
        action_target_path = ""
        action_target_symbol = ""
        action_identity_samples = 0
    action_median = median(actions) if actions else 0.0
    wrapper_median = median(wrappers) if wrappers else 0.0
    total_median = median(totals) if totals else 0.0
    confirmed = len(rows) >= max(1, int(minimum_samples))

    if not confirmed:
        conclusion = "Yerel runtime kaniti yetersiz; en az 3 olculebilir tekrar gerekli."
    elif action_median >= max(wrapper_median * 3.0, wrapper_median + 1.0):
        if action_target_path or action_target_symbol:
            conclusion = (
                "Darbogaz wrapper katmaninda degil; olculen surenin baskin bolumu "
                "sarilan action icinde. Bir sonraki yerel hedef: "
                f"{action_target_path or '(bilinmiyor)'} - "
                f"{action_target_symbol or '(bilinmiyor)'}. "
                "Wrapper optimizasyonu icin kok neden kaniti yok."
            )
        else:
            conclusion = (
                "Darbogaz wrapper katmaninda degil; olculen surenin baskin bolumu "
                "sarilan action icinde. Eski runtime olaylarinda action kimligi yok; "
                "alt hedefi secmek icin yeni kimlikli runtime ornekleri gerekli. "
                "Wrapper optimizasyonu icin kok neden kaniti yok."
            )
    elif wrapper_median >= max(action_median, 1.0):
        conclusion = (
            "Wrapper overhead yerel olcumde anlamli gorunuyor. Patch oncesinde "
            "wrapper icindeki alt adimlar ayrica daraltilmali."
        )
    else:
        conclusion = (
            "Action ve wrapper sureleri birbirine yakin; daha dar yerel olcum gerekli."
        )

    return LocalRuntimeValidationReport(
        finding.finding_id,
        path,
        symbol,
        len(rows),
        action_median,
        wrapper_median,
        total_median,
        conclusion,
        confirmed,
        rows[-1][0].created_at if rows else "",
        action_target_path=action_target_path,
        action_target_symbol=action_target_symbol,
        action_identity_samples=action_identity_samples,
    )
