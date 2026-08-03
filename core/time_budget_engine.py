from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(text: object) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.split())


def _minutes_from_text(text: object) -> int | None:
    value = _normalize(text)
    matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*(saat|dakika|dk|hour|minute|min)", value)
    if not matches:
        return None
    total = 0.0
    for raw, unit in matches:
        amount = float(raw.replace(",", "."))
        total += amount * 60 if unit in {"saat", "hour"} else amount
    return max(1, round(total))


def asks_for_time_estimate(text: object) -> bool:
    value = _normalize(text)
    return any(marker in value for marker in (
        "ne kadar surer", "kac saat surer", "kac dakika surer",
        "tahmini sure", "ne zamana biter", "ne zaman tamamlarsin",
    ))


def parse_time_budget(text: object) -> int | None:
    value = _normalize(text)
    minutes = _minutes_from_text(value)
    if minutes is None:
        return None
    budget_markers = (
        "suren var", "vaktin var", "icinde bitir", "en fazla", "bu surede",
        "saatin var", "dakikan var", "teslim et", "bitirmen gerekiyor",
    )
    return minutes if any(marker in value for marker in budget_markers) else None


def asks_for_time_plan(text: object) -> bool:
    value = _normalize(text)
    return any(marker in value for marker in (
        "zaman planini goster", "sure planini goster", "neleri erteleyeceksin",
        "onceliklerini goster", "bu sureye nasil sigdiracaksin",
    ))


@dataclass(slots=True)
class TimeBudgetPlan:
    plan_id: str
    task: str
    estimate_low_minutes: int
    estimate_likely_minutes: int
    estimate_high_minutes: int
    confidence: float
    created_at: str
    budget_minutes: int = 0
    strategy: str = "full"
    required_now: list[str] = field(default_factory=list)
    defer: list[str] = field(default_factory=list)
    unnecessary: list[str] = field(default_factory=list)
    validation: list[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    actual_minutes: int = 0

    def estimate_report(self) -> str:
        likely = _format_minutes(self.estimate_likely_minutes)
        low = _format_minutes(self.estimate_low_minutes)
        high = _format_minutes(self.estimate_high_minutes)
        return (
            f"Bu görevin tam ve güvenli çözümü tahminen {likely} sürer. "
            f"En iyi durumda {low}, beklenmeyen sorun çıkarsa {high} sürebilir. "
            "Daha kısa bir süre verirsen kapsamı daraltıp minimum güvenli teslimatı ayrıca planlayabilirim."
        )

    def budget_report(self) -> str:
        lines = [
            f"Tamam. Süre bütçem {_format_minutes(self.budget_minutes)}. "
            f"Tam kapsam tahminim {_format_minutes(self.estimate_likely_minutes)} olduğu için "
            f"'{self.strategy}' teslimat stratejisini kullanacağım.",
            "",
            "Şimdi gerekli:",
            *[f"- {item}" for item in self.required_now],
            "",
            "Sonraya bırakılacak:",
            *[f"- {item}" for item in self.defer],
            "",
            "Bu görev için gereksiz:",
            *[f"- {item}" for item in self.unnecessary],
            "",
            "Güvenlik sınırı:",
            *[f"- {item}" for item in self.validation],
            "",
            "Süreyi karşılamak için testleri sessizce atlamayacağım; tam regresyon sığmazsa bunu açıkça erteleyeceğim.",
        ]
        return "\n".join(lines)


def _format_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} dakika"
    hours, rem = divmod(minutes, 60)
    if rem == 0:
        return f"{hours} saat"
    return f"{hours} saat {rem} dakika"


class TimeBudgetStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self._lock = threading.RLock()

    def load(self) -> TimeBudgetPlan | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            return TimeBudgetPlan(**payload)
        except (OSError, TypeError, ValueError, UnicodeError):
            return None

    def save(self, plan: TimeBudgetPlan) -> TimeBudgetPlan:
        encoded = json.dumps(asdict(plan), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        with self._lock:
            try:
                with tempfile.NamedTemporaryFile("wb", dir=self.path.parent, delete=False, prefix=".time-budget-", suffix=".tmp") as handle:
                    handle.write(encoded + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary = Path(handle.name)
                os.replace(temporary, self.path)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return plan

    def estimate(self, task: object, *, complexity_hint: str = "") -> TimeBudgetPlan:
        text = " ".join(str(task or "").split()) or "Son konuşmadaki görev"
        normalized = _normalize(text + " " + complexity_hint)
        likely = 45
        if any(word in normalized for word in ("arastir", "analiz", "incele")):
            likely += 25
        if any(word in normalized for word in ("kod", "duzelt", "gelistir", "refaktor", "uygula")):
            likely += 45
        if any(word in normalized for word in ("test", "regresyon", "dogrula")):
            likely += 35
        if any(word in normalized for word in ("tum", "tam", "butun", "mimari", "birden fazla")):
            likely += 50
        low = max(15, round(likely * 0.65))
        high = max(likely + 15, round(likely * 1.6))
        return self.save(TimeBudgetPlan(
            plan_id="TB-" + uuid.uuid4().hex[:10].upper(),
            task=text[:3000],
            estimate_low_minutes=low,
            estimate_likely_minutes=likely,
            estimate_high_minutes=high,
            confidence=0.62,
            created_at=_now(),
        ))

    def apply_budget(self, minutes: int) -> TimeBudgetPlan | None:
        plan = self.load()
        if plan is None:
            return None
        ratio = minutes / max(1, plan.estimate_likely_minutes)
        if ratio >= 1:
            strategy = "tam kapsam"
            required = ["İstenen çözümü tamamlamak", "İlgili testleri çalıştırmak", "Sonucu ve riski raporlamak"]
            defer = ["Yalnızca ek iyileştirme fırsatları"]
        elif ratio >= 0.3:
            strategy = "minimum güvenli teslimat"
            required = ["Sorunu ve kritik yolu doğrulamak", "Çalışan en küçük çözümü uygulamak", "Derleme ve odaklı testleri çalıştırmak", "Geri alma noktasını korumak"]
            defer = ["Tam regresyon paketi", "Ek optimizasyonlar", "Kapsamlı dokümantasyon", "İlgisiz kod temizliği"]
        else:
            strategy = "teşhis ve uygulanabilir plan"
            required = ["Kök nedeni daraltmak", "Kanıtlı çözüm planı hazırlamak", "Risk ve test kapsamını belirlemek"]
            defer = ["Kaynak kod değişikliği", "Tam test çalıştırması", "Optimizasyon ve dokümantasyon"]
        plan.budget_minutes = minutes
        plan.strategy = strategy
        plan.required_now = required
        plan.defer = defer
        plan.unnecessary = ["İstekle ilgisiz refaktör", "Yeni özellik eklemek", "Sırf estetik amaçlı değişiklik"]
        plan.validation = ["Derleme kontrolünü atlama", "Kritik odaklı testi atlama", "Onaysız kapsam genişletme", "Başarısız değişiklikte geri alma"]
        return self.save(plan)
