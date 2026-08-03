from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

_ALLOWED_STATES = {"researching", "solution_found", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(text: object) -> str:
    value = str(text or "").casefold()
    table = str.maketrans("çğıöşüâîû", "cgiosuaiu")
    return " ".join(value.translate(table).split())


def looks_like_self_improvement_complaint(text: object) -> bool:
    """Recognize natural complaints that ask Jarvis to investigate itself."""

    normalized = _normalize(text)
    if not normalized:
        return False
    complaint = any(
        marker in normalized
        for marker in (
            "cok yavas",
            "yavas dusun",
            "yavas cevap",
            "gec cevap",
            "uzun suruyor",
            "bekletiyorsun",
            "agir calis",
        )
    )
    self_target = any(
        marker in normalized
        for marker in (
            "kendini gelistir",
            "kendini iyilestir",
            "ne yapabilirsin",
            "nasil hizlan",
            "bunu arastir",
            "cozum bul",
            "neden yavas",
        )
    )
    return complaint and self_target


def asks_for_self_improvement_result(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "ne buldun",
            "sonuc buldun mu",
            "arastirma sonucu",
            "cozum buldun mu",
            "yavaslik arastirmasi",
            "kendini gelistirme arastirmasi",
        )
    )


@dataclass(frozen=True, slots=True)
class SelfImprovementResearchTask:
    task_id: str
    complaint: str
    state: str
    created_at: str
    completed_at: str = ""
    summary: str = ""
    cause: str = ""
    solution: str = ""
    benefit: str = ""
    risk: str = ""
    affected_paths: tuple[str, ...] = ()
    validation: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["affected_paths"] = list(self.affected_paths)
        payload["validation"] = list(self.validation)
        payload["evidence_ids"] = list(self.evidence_ids)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SelfImprovementResearchTask":
        state = str(payload.get("state", ""))
        if state not in _ALLOWED_STATES:
            raise ValueError("invalid self-improvement research state")
        return cls(
            task_id=str(payload.get("task_id", "")),
            complaint=str(payload.get("complaint", "")),
            state=state,
            created_at=str(payload.get("created_at", "")),
            completed_at=str(payload.get("completed_at", "")),
            summary=str(payload.get("summary", "")),
            cause=str(payload.get("cause", "")),
            solution=str(payload.get("solution", "")),
            benefit=str(payload.get("benefit", "")),
            risk=str(payload.get("risk", "")),
            affected_paths=tuple(str(item) for item in payload.get("affected_paths", ()) if item),
            validation=tuple(str(item) for item in payload.get("validation", ()) if item),
            evidence_ids=tuple(str(item) for item in payload.get("evidence_ids", ()) if item),
            error=str(payload.get("error", "")),
        )

    def user_report(self) -> str:
        if self.state == "researching":
            return (
                "Yavaşlık şikâyetini araştırıyorum. Önce hangi aşamanın süreyi "
                "tükettiğini ölçüp, sonra güvenli çözüm seçeneklerini karşılaştıracağım. "
                "Henüz kodumu değiştirmedim."
            )
        if self.state == "failed":
            return (
                "Araştırmayı tamamlayamadım. "
                + (self.error or "Yeterli kanıt toplanamadı.")
                + " Kodumu değiştirmedim."
            )
        paths = ", ".join(self.affected_paths[:4]) or "henüz kesin dosya kapsamı yok"
        checks = "; ".join(self.validation[:3]) or "önce/sonra süre karşılaştırması"
        return (
            "Yavaşlık araştırmasını tamamladım.\n\n"
            f"Ne buldum: {self.summary}\n"
            f"Muhtemel neden: {self.cause}\n"
            f"Önerdiğim çözüm: {self.solution}\n"
            f"Beklenen fayda: {self.benefit}\n"
            f"Risk: {self.risk}\n"
            f"İlgili kapsam: {paths}\n"
            f"Nasıl doğrulayacağım: {checks}\n\n"
            "Bu yalnızca araştırma sonucudur; henüz hiçbir dosyayı değiştirmedim. "
            "İstersen bu çözüm için önce teknik plan hazırlayabilirim."
        )


class SelfImprovementResearchStore:
    MAX_BYTES = 256 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self._lock = threading.RLock()

    def load(self) -> SelfImprovementResearchTask | None:
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > self.MAX_BYTES:
                return None
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            task = SelfImprovementResearchTask.from_dict(payload)
            return task if task.task_id and task.created_at else None
        except (OSError, ValueError, TypeError, UnicodeError):
            return None

    def save(self, task: SelfImprovementResearchTask) -> None:
        if task.state not in _ALLOWED_STATES:
            raise ValueError("invalid self-improvement research state")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            task.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > self.MAX_BYTES:
            raise ValueError("self-improvement research payload is too large")
        with self._lock:
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    handle.write(encoded)
                    handle.write(b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary = Path(handle.name)
                os.replace(temporary, self.path)
                temporary = None
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink(missing_ok=True)

    def start(self, complaint: str) -> SelfImprovementResearchTask:
        task = SelfImprovementResearchTask(
            task_id="SIR-" + uuid.uuid4().hex[:10].upper(),
            complaint=" ".join(str(complaint or "").split())[:2000],
            state="researching",
            created_at=_now(),
        )
        self.save(task)
        return task

    def complete(
        self,
        task: SelfImprovementResearchTask,
        *,
        summary: str,
        cause: str,
        solution: str,
        benefit: str,
        risk: str,
        affected_paths: Iterable[str] = (),
        validation: Iterable[str] = (),
        evidence_ids: Iterable[str] = (),
    ) -> SelfImprovementResearchTask:
        completed = replace(
            task,
            state="solution_found",
            completed_at=_now(),
            summary=str(summary).strip()[:2000],
            cause=str(cause).strip()[:3000],
            solution=str(solution).strip()[:3000],
            benefit=str(benefit).strip()[:2000],
            risk=str(risk).strip()[:2000],
            affected_paths=tuple(dict.fromkeys(str(item) for item in affected_paths if item))[:12],
            validation=tuple(dict.fromkeys(str(item) for item in validation if item))[:12],
            evidence_ids=tuple(dict.fromkeys(str(item) for item in evidence_ids if item))[:20],
            error="",
        )
        self.save(completed)
        return completed

    def fail(self, task: SelfImprovementResearchTask, error: object) -> SelfImprovementResearchTask:
        failed = replace(
            task,
            state="failed",
            completed_at=_now(),
            error=" ".join(str(error or "").split())[:2000],
        )
        self.save(failed)
        return failed


def choose_speed_research_result(runtime_report: object, architecture_assessment: object) -> dict[str, object]:
    """Convert technical evidence into a bounded, plain-language recommendation."""

    findings = tuple(getattr(runtime_report, "findings", ()) or ())
    slow = [item for item in findings if getattr(item, "category", "") == "repeated_slow_operation"]
    if slow:
        chosen = max(
            slow,
            key=lambda item: (
                int(getattr(item, "occurrence_count", 0)),
                float(getattr(item, "confidence", 0.0)),
            ),
        )
        paths = tuple(getattr(chosen, "affected_paths", ()) or ())
        symbols = tuple(getattr(chosen, "affected_symbols", ()) or ())
        target = ", ".join(symbols[:2]) or str(getattr(chosen, "title", "yavaş işlem"))
        return {
            "summary": f"Yavaşlık genel bir tahmin değil; tekrar eden bir işlemde ölçülmüş: {target}.",
            "cause": str(getattr(chosen, "explanation", "İşlem kendi süre eşiğini tekrar tekrar aşıyor.")),
            "solution": (
                "Bu işlemi aşamalara ayırıp her aşamanın süresini ayrı ölçmek; "
                "yalnızca en çok zaman tüketen adımı optimize etmek."
            ),
            "benefit": "Gereksiz genel refaktör yerine gerçek darboğaza odaklanarak cevap süresini düşürmek.",
            "risk": "Yanlış aşama optimize edilirse hız kazanımı olmaz; bu nedenle önce ölçüm eklenmeli.",
            "affected_paths": paths,
            "validation": tuple(getattr(chosen, "acceptance_criteria", ()) or ()),
            "evidence_ids": (str(getattr(chosen, "finding_id", "")),),
        }

    architecture_findings = tuple(getattr(architecture_assessment, "findings", ()) or ())
    relevant = [
        item for item in architecture_findings
        if getattr(item, "category", "") in {"complexity_hotspot", "large_module", "dependency_hotspot"}
        and any(
            marker in (str(getattr(item, "title", "")) + " " + " ".join(getattr(item, "affected_paths", ()) or ())).casefold()
            for marker in ("assistant", "dialogue", "model", "research", "command")
        )
    ]
    chosen = relevant[0] if relevant else None
    paths = tuple(getattr(chosen, "affected_paths", ()) or ()) if chosen is not None else ()
    evidence_id = str(getattr(chosen, "finding_id", "")) if chosen is not None else ""
    return {
        "summary": (
            "Mevcut kayıtlar, yavaşlığın tek bir aşamada tekrarlandığını henüz kanıtlamıyor. "
            "Bu yüzden büyük dosya veya karmaşıklık bulgularını doğrudan neden saymak güvenli değil."
        ),
        "cause": (
            "Komut yönlendirme, bağlam hazırlama, model çağrısı ve cevap işleme süreleri ayrı ayrı "
            "ölçülmediği için gerçek darboğaz görünmüyor."
        ),
        "solution": (
            "Önce bu dört aşamaya düşük maliyetli süre ölçümü eklemek; birkaç gerçek konuşmadan sonra "
            "en yavaş aşamayı seçip yalnızca onu iyileştirmek."
        ),
        "benefit": "Tahmine dayalı kod bölmek yerine kanıtlı darboğazı bulmak ve anlaşılır bir önce/sonra raporu üretmek.",
        "risk": "Ölçüm çok ayrıntılı tutulursa günlükler büyüyebilir; kayıtlar sınırlı ve kişisel içerikten arındırılmış olmalı.",
        "affected_paths": paths or ("core/assistant.py", "core/local_dialogue.py"),
        "validation": (
            "Aynı kullanıcı isteği en az üç kez ölçülmeli.",
            "Aşamaların ortanca süreleri önce ve sonra karşılaştırılmalı.",
            "Cevap içeriği, iptal ve ilerleme bildirimi davranışı korunmalı.",
        ),
        "evidence_ids": (evidence_id,) if evidence_id else (),
    }
