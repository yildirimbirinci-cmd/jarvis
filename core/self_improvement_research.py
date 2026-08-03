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

_ALLOWED_STATES = {"queued", "researching", "solution_found", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(text: object) -> str:
    value = str(text or "").casefold()
    table = str.maketrans("çğıöşüâîû", "cgiosuaiu")
    return " ".join(value.translate(table).split())




def looks_like_opened_item_followup(text: str) -> bool:
    """Return True only for an explicit question about what was opened.

    Prefix matching is intentionally avoided: Turkish words such as ``neden``
    and ``açık`` previously looked like ``ne`` + ``aç`` and caused ordinary
    research instructions to be mistaken for an editor-open follow-up.
    """
    normalized = _normalize(text)
    phrases = (
        "az once ne actin",
        "az once neyi actin",
        "az once hangisini actin",
        "hangi klasoru actin",
        "hangi dosyayi actin",
        "neyi actin",
        "ne actin",
        "hangisini actin",
    )
    return any(phrase in normalized for phrase in phrases)

def looks_like_self_improvement_complaint(text: object) -> bool:
    """Recognize natural complaints that ask Jarvis to investigate itself."""

    normalized = _normalize(text)
    if not normalized:
        return False
    tokens = set(normalized.split())
    complaint = (
        any(token.startswith("yavas") for token in tokens)
        or any(
            marker in normalized
            for marker in (
                "gec cevap",
                "uzun suruyor",
                "fazla suruyor",
                "bekletiyorsun",
                "agir calis",
                "hizli degil",
            )
        )
    )
    self_target = (
        any(
            marker in normalized
            for marker in (
                "kendini gelistir",
                "kendini iyilestir",
                "ne yapabilirsin",
                "nasil hizlan",
                "nedenini arastir",
                "nedenlerini arastir",
                "bunu arastir",
                "bunun neden",
                "cozum bul",
                "neden yavas",
                "problemi anla",
            )
        )
        or ("arastir" in tokens and any(token.startswith("neden") for token in tokens))
    )
    return complaint and self_target




def asks_to_cancel_self_improvement_research(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "arastirmayi durdur",
            "arastirmayi iptal et",
            "incelemeyi durdur",
            "bunu arastirmayi birak",
        )
    )


def asks_to_restart_self_improvement_research(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "arastirmayi bastan baslat",
            "bastan arastir",
            "yeniden arastir",
            "arastirmayi tekrar baslat",
        )
    )

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


def asks_for_self_improvement_status(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "arastirma ne durumda",
            "arastirma devam ediyor mu",
            "ne asamadasin",
            "inceleme ne durumda",
        )
    )


def asks_for_self_improvement_journal(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "arastirma gunlugunu goster",
            "gelisim gunlugunu goster",
            "hangi hipotezleri denedin",
            "neleri eledin",
            "arastirma adimlarini goster",
        )
    )


def asks_for_self_improvement_plan(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "gelisim plani hazirla",
            "iyilestirme plani hazirla",
            "cozum plani hazirla",
            "bu cozum icin plan hazirla",
            "secenekleri karsilastir",
            "nasil uygulayacagini planla",
        )
    )


def asks_for_self_improvement_technical_details(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "teknik ayrintilari goster",
            "teknik detaylari goster",
            "ayrintili raporu goster",
            "kanitlari goster",
            "hangi fonksiyonlar",
        )
    )


@dataclass(frozen=True, slots=True)
class SelfImprovementResearchTask:
    task_id: str
    complaint: str
    state: str
    created_at: str
    completed_at: str = ""
    stage: str = "queued"
    progress: int = 0
    status_message: str = ""
    summary: str = ""
    cause: str = ""
    solution: str = ""
    benefit: str = ""
    risk: str = ""
    affected_paths: tuple[str, ...] = ()
    validation: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    technical_details: tuple[str, ...] = ()
    journal_entries: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    related_research_ids: tuple[str, ...] = ()
    related_experience_ids: tuple[str, ...] = ()
    experience_context: str = ""
    plan_options: tuple[str, ...] = ()
    recommended_option: str = ""
    implementation_plan: tuple[str, ...] = ()
    test_plan: tuple[str, ...] = ()
    plan_created_at: str = ""
    feedback_category: str = "performance"
    reflection_confidence: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["affected_paths"] = list(self.affected_paths)
        payload["validation"] = list(self.validation)
        payload["evidence_ids"] = list(self.evidence_ids)
        payload["technical_details"] = list(self.technical_details)
        payload["journal_entries"] = list(self.journal_entries)
        payload["hypotheses"] = list(self.hypotheses)
        payload["related_research_ids"] = list(self.related_research_ids)
        payload["related_experience_ids"] = list(self.related_experience_ids)
        payload["plan_options"] = list(self.plan_options)
        payload["implementation_plan"] = list(self.implementation_plan)
        payload["test_plan"] = list(self.test_plan)
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
            stage=str(payload.get("stage", "queued")),
            progress=max(0, min(100, int(payload.get("progress", 0) or 0))),
            status_message=str(payload.get("status_message", "")),
            summary=str(payload.get("summary", "")),
            cause=str(payload.get("cause", "")),
            solution=str(payload.get("solution", "")),
            benefit=str(payload.get("benefit", "")),
            risk=str(payload.get("risk", "")),
            affected_paths=tuple(str(item) for item in payload.get("affected_paths", ()) if item),
            validation=tuple(str(item) for item in payload.get("validation", ()) if item),
            evidence_ids=tuple(str(item) for item in payload.get("evidence_ids", ()) if item),
            technical_details=tuple(str(item) for item in payload.get("technical_details", ()) if item),
            journal_entries=tuple(str(item) for item in payload.get("journal_entries", ()) if item),
            hypotheses=tuple(str(item) for item in payload.get("hypotheses", ()) if item),
            related_research_ids=tuple(str(item) for item in payload.get("related_research_ids", ()) if item),
            related_experience_ids=tuple(str(item) for item in payload.get("related_experience_ids", ()) if item),
            experience_context=str(payload.get("experience_context", "")),
            plan_options=tuple(str(item) for item in payload.get("plan_options", ()) if item),
            recommended_option=str(payload.get("recommended_option", "")),
            implementation_plan=tuple(str(item) for item in payload.get("implementation_plan", ()) if item),
            test_plan=tuple(str(item) for item in payload.get("test_plan", ()) if item),
            plan_created_at=str(payload.get("plan_created_at", "")),
            feedback_category=str(payload.get("feedback_category", "performance")),
            reflection_confidence=max(0.0, min(1.0, float(payload.get("reflection_confidence", 0.0) or 0.0))),
            error=str(payload.get("error", "")),
        )

    def status_report(self) -> str:
        if self.state == "solution_found":
            return "Araştırma tamamlandı. Sonucu dinlemek için 'ne buldun' diyebilirsin."
        if self.state == "failed":
            return "Araştırma tamamlanamadı. Ayrıntı için 'ne buldun' diyebilirsin."
        if self.state == "cancelled":
            return "Araştırma durduruldu. Yeniden başlatmamı istersen 'baştan araştır' diyebilirsin."
        message = self.status_message or "Yavaşlığın hangi aşamada oluştuğunu inceliyorum."
        return f"Araştırma devam ediyor. %{self.progress}: {message} Henüz kodumu değiştirmedim."

    def user_report(self) -> str:
        if self.state in {"queued", "researching"}:
            return self.status_report()
        if self.state == "cancelled":
            return "Araştırmayı kullanıcı isteğiyle durdurdum. Hiçbir dosyayı değiştirmedim."
        if self.state == "failed":
            return (
                "Araştırmayı tamamlayamadım. "
                + (self.error or "Yeterli kanıt toplanamadı.")
                + " Kodumu değiştirmedim."
            )
        return (
            "Araştırmayı tamamladım.\n\n"
            f"Ne buldum: {self.summary}\n"
            f"Bunun anlamı: {self.cause}\n"
            f"Önerdiğim çözüm: {self.solution}\n"
            f"Beklenen fayda: {self.benefit}\n"
            f"Risk: {self.risk}\n\n"
            "henüz hiçbir dosyayı değiştirmedim. Bu çözüm için plan hazırlamamı "
            "isteyebilirsin. Teknik kayıtları görmek için 'teknik ayrıntıları göster' de."
        )

    def plan_report(self) -> str:
        if self.state != "solution_found":
            return "Plan hazırlayabilmem için araştırmanın tamamlanması gerekiyor. " + self.status_report()
        if not self.plan_options:
            return (
                "Araştırma tamamlandı ancak henüz bir iyileştirme planı hazırlamadım. "
                "'Bu çözüm için plan hazırla' diyebilirsin."
            )
        options = "\n".join(f"{index}. {item}" for index, item in enumerate(self.plan_options, 1))
        steps = "\n".join(f"- {item}" for item in self.implementation_plan) or "- Henüz uygulama adımı yok."
        tests = "\n".join(f"- {item}" for item in self.test_plan) or "- Henüz test adımı yok."
        return (
            "KENDİNİ GELİŞTİRME PLANI\n\n"
            f"Değerlendirilen seçenekler:\n{options}\n\n"
            f"Önerdiğim seçenek: {self.recommended_option}\n\n"
            f"Uygulama adımları:\n{steps}\n\n"
            f"Doğrulama planı:\n{tests}\n\n"
            "Henüz patch üretmedim ve hiçbir dosyayı değiştirmedim. "
            "Planı uygun bulursan açıkça onaylayabilirsin."
        )

    def journal_report(self) -> str:
        if not self.journal_entries:
            return "Bu araştırma için henüz günlük kaydı oluşmadı."
        entries = "\n".join(f"- {item}" for item in self.journal_entries[-16:])
        hypotheses = "\n".join(f"- {item}" for item in self.hypotheses[:10])
        if not hypotheses:
            hypotheses = "- Henüz sonuçlandırılmış hipotez yok."
        related = ", ".join(self.related_research_ids) or "benzer geçmiş araştırma bulunmadı"
        experiences = ", ".join(self.related_experience_ids) or "benzer geçmiş deneyim bulunmadı"
        return (
            "KENDİNİ GELİŞTİRME ARAŞTIRMA GÜNLÜĞÜ\n\n"
            f"Araştırma: {self.task_id}\n"
            f"Benzer geçmiş kayıtlar: {related}\n"
            f"Benzer deneyimler: {experiences}\n"
            f"Geçmişten karar desteği: {self.experience_context or 'yok'}\n\n"
            f"Hipotezler ve sonuçları:\n{hypotheses}\n\n"
            f"Zaman sıralı adımlar:\n{entries}\n\n"
            "Bu günlük tanı ve öğrenme içindir; tek başına kod değişikliği onayı değildir."
        )

    def technical_report(self) -> str:
        if self.state != "solution_found":
            return self.status_report()
        paths = ", ".join(self.affected_paths[:8]) or "kesin dosya kapsamı yok"
        checks = "; ".join(self.validation[:6]) or "önce/sonra süre karşılaştırması"
        details = "\n".join(f"- {item}" for item in self.technical_details[:12])
        if not details:
            details = "- Ek teknik olay kaydı bulunmuyor."
        return (
            "TEKNİK ARAŞTIRMA AYRINTILARI\n\n"
            f"İlgili kapsam: {paths}\n"
            f"Doğrulama: {checks}\n"
            f"Kanıt kimlikleri: {', '.join(self.evidence_ids) or 'yok'}\n"
            f"Kayıtlar:\n{details}\n\n"
            "Bu rapor yalnızca tanı içindir; kod değişikliği uygulanmadı."
        )



class SelfImprovementResearchStore:
    MAX_BYTES = 256 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.history_path = self.path.with_name(self.path.stem + "_history.json")
        self.experience_store = None
        self._lock = threading.RLock()

    def _load_history(self) -> list[SelfImprovementResearchTask]:
        if not self.history_path.exists():
            return []
        try:
            if self.history_path.stat().st_size > self.MAX_BYTES * 4:
                return []
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return []
            tasks: list[SelfImprovementResearchTask] = []
            for item in payload[-50:]:
                if isinstance(item, dict):
                    try:
                        tasks.append(SelfImprovementResearchTask.from_dict(item))
                    except (ValueError, TypeError):
                        continue
            return tasks
        except (OSError, ValueError, TypeError, UnicodeError):
            return []

    def _save_history(self, tasks: Iterable[SelfImprovementResearchTask]) -> None:
        payload = [task.to_dict() for task in tuple(tasks)[-50:]]
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        if len(encoded) > self.MAX_BYTES * 4:
            payload = payload[-20:]
            encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.history_path.parent,
                prefix=f".{self.history_path.name}.", suffix=".tmp", delete=False
            ) as handle:
                handle.write(encoded)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.history_path)
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        ignored = {"bir", "ve", "bu", "icin", "neden", "arastir", "jarvis", "son", "zamanlarda"}
        a = {token for token in _normalize(left).split() if len(token) > 2 and token not in ignored}
        b = {token for token in _normalize(right).split() if len(token) > 2 and token not in ignored}
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def find_similar(self, complaint: str, *, limit: int = 3) -> tuple[SelfImprovementResearchTask, ...]:
        ranked = [
            (self._similarity(complaint, task.complaint), task)
            for task in self._load_history()
            if task.state == "solution_found"
        ]
        ranked.sort(key=lambda item: item[0], reverse=True)
        return tuple(task for score, task in ranked if score >= 0.20)[:limit]

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

    def start(
        self, complaint: str, *, feedback_category: str = "performance",
        reflection_confidence: float = 0.0,
    ) -> SelfImprovementResearchTask:
        similar = self.find_similar(complaint)
        experiences = ()
        experience_context = ""
        if self.experience_store is not None:
            experiences = self.experience_store.find_similar(complaint, feedback_category)
            experience_context = self.experience_store.context_message(experiences)
        now = _now()
        task = SelfImprovementResearchTask(
            task_id="SIR-" + uuid.uuid4().hex[:10].upper(),
            complaint=" ".join(str(complaint or "").split())[:2000],
            state="queued",
            created_at=now,
            stage="queued",
            progress=0,
            status_message="Araştırma görevi sıraya alındı.",
            journal_entries=(f"{now} — Araştırma görevi oluşturuldu.",),
            related_research_ids=tuple(item.task_id for item in similar),
            related_experience_ids=tuple(item.experience_id for item in experiences),
            experience_context=experience_context,
            feedback_category=str(feedback_category or "performance")[:80],
            reflection_confidence=max(0.0, min(1.0, float(reflection_confidence))),
        )
        self.save(task)
        return task

    def update_progress(
        self,
        task: SelfImprovementResearchTask,
        *,
        stage: str,
        progress: int,
        status_message: str,
    ) -> SelfImprovementResearchTask:
        updated = replace(
            task,
            state="researching",
            stage=str(stage).strip()[:80] or "researching",
            progress=max(0, min(99, int(progress))),
            status_message=" ".join(str(status_message or "").split())[:500],
            journal_entries=task.journal_entries + (
                f"{_now()} — %{max(0, min(99, int(progress)))} {str(status_message or '').strip()}",
            ),
        )
        self.save(updated)
        return updated

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
        technical_details: Iterable[str] = (),
        hypotheses: Iterable[str] = (),
    ) -> SelfImprovementResearchTask:
        completed = replace(
            task,
            state="solution_found",
            completed_at=_now(),
            stage="completed",
            progress=100,
            status_message="Araştırma tamamlandı.",
            summary=str(summary).strip()[:2000],
            cause=str(cause).strip()[:3000],
            solution=str(solution).strip()[:3000],
            benefit=str(benefit).strip()[:2000],
            risk=str(risk).strip()[:2000],
            affected_paths=tuple(dict.fromkeys(str(item) for item in affected_paths if item))[:12],
            validation=tuple(dict.fromkeys(str(item) for item in validation if item))[:12],
            evidence_ids=tuple(dict.fromkeys(str(item) for item in evidence_ids if item))[:20],
            technical_details=tuple(dict.fromkeys(str(item) for item in technical_details if item))[:30],
            hypotheses=tuple(dict.fromkeys(str(item) for item in hypotheses if item))[:12],
            journal_entries=task.journal_entries + (f"{_now()} — Araştırma tamamlandı ve çözüm seçildi.",),
            error="",
        )
        self.save(completed)
        if self.experience_store is not None:
            self.experience_store.remember_research(completed)
        with self._lock:
            history = [item for item in self._load_history() if item.task_id != completed.task_id]
            history.append(completed)
            self._save_history(history)
        return completed

    def prepare_plan(self, task: SelfImprovementResearchTask) -> SelfImprovementResearchTask:
        if task.state != "solution_found":
            raise ValueError("research must be completed before planning")
        primary_scope = ", ".join(task.affected_paths[:3]) or "ilgili çalışma zamanı bileşenleri"
        options = (
            "Önce yalnızca ölçüm ekle — risk düşük; kök nedeni kanıtlar, doğrudan hız kazancı sağlamaz.",
            f"Kanıtlı darboğaza küçük ve hedefli müdahale yap — risk düşük-orta; kapsam: {primary_scope}.",
            "Arka plan/önbellek mimarisi kur — olası kazanç yüksek; kapsam ve regresyon riski daha büyük.",
        )
        recommendation = (
            "2. seçenek. Araştırmada çalışma zamanı kanıtı bulunduğu için, önce küçük ve geri alınabilir "
            "bir müdahaleyi geçici çalışma alanında sınamak en dengeli yaklaşım."
            if task.evidence_ids
            else
            "1. seçenek. Çalışma zamanı kök nedeni henüz kesin olmadığı için önce düşük maliyetli ölçüm eklemek en güvenli yaklaşım."
        )
        implementation = (
            "Araştırma kanıtlarını ve hedef sembolleri yeniden doğrula.",
            f"Değişikliği yalnızca {primary_scope} kapsamıyla sınırla.",
            "Patch'i geçici worktree üzerinde üret ve uygula.",
            "Davranış, iptal, ilerleme ve hata yollarını karşılaştır.",
            "Sonuç başarılıysa gerçek çalışma ağacına uygulamak için ayrıca onay iste.",
        )
        tests = tuple(task.validation) or (
            "Aynı kullanıcı isteğini en az üç kez önce/sonra ölç.",
            "İlgili birim ve regresyon testlerini çalıştır.",
            "Başarısızlıkta worktree'yi sil ve gerçek dosyaları değiştirme.",
        )
        updated = replace(
            task,
            plan_options=options,
            recommended_option=recommendation,
            implementation_plan=implementation,
            test_plan=tests,
            plan_created_at=_now(),
            journal_entries=task.journal_entries + (f"{_now()} — Araştırma sonucu için iyileştirme planı hazırlandı.",),
        )
        self.save(updated)
        return updated

    def cancel(self, task: SelfImprovementResearchTask, reason: str = "Kullanıcı araştırmayı durdurdu.") -> SelfImprovementResearchTask:
        if task.state not in {"queued", "researching"}:
            return task
        cancelled = replace(
            task,
            state="cancelled",
            completed_at=_now(),
            stage="cancelled",
            status_message="Araştırma kullanıcı isteğiyle durduruldu.",
            journal_entries=task.journal_entries + (f"{_now()} — Araştırma kullanıcı isteğiyle durduruldu.",),
            error=" ".join(str(reason or "").split())[:1000],
        )
        self.save(cancelled)
        return cancelled

    def is_active(self, task_id: str) -> bool:
        current = self.load()
        return bool(current is not None and current.task_id == task_id and current.state in {"queued", "researching"})

    def fail(self, task: SelfImprovementResearchTask, error: object) -> SelfImprovementResearchTask:
        failed = replace(
            task,
            state="failed",
            completed_at=_now(),
            error=" ".join(str(error or "").split())[:2000],
            journal_entries=task.journal_entries + (f"{_now()} — Araştırma hata nedeniyle tamamlanamadı.",),
        )
        self.save(failed)
        return failed


def choose_speed_research_result(runtime_report: object, architecture_assessment: object) -> dict[str, object]:
    """Convert technical evidence into a bounded, plain-language recommendation."""

    findings = tuple(getattr(runtime_report, "findings", ()) or ())
    deduplicated: dict[tuple[str, tuple[str, ...], tuple[str, ...]], object] = {}
    for item in findings:
        key = (
            str(getattr(item, "category", "")),
            tuple(getattr(item, "affected_paths", ()) or ()),
            tuple(getattr(item, "affected_symbols", ()) or ()),
        )
        previous = deduplicated.get(key)
        if previous is None or (
            int(getattr(item, "occurrence_count", 0)),
            float(getattr(item, "confidence", 0.0)),
        ) > (
            int(getattr(previous, "occurrence_count", 0)),
            float(getattr(previous, "confidence", 0.0)),
        ):
            deduplicated[key] = item
    findings = tuple(deduplicated.values())
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
            "hypotheses": (
                f"Görev yürütme darboğazı — desteklendi: {target} tekrar eden süre aşımı üretti.",
                "Ses işleme ana neden — bu araştırmada normal sohbet yavaşlığını açıklayan yeterli kanıt bulunmadı.",
                "Statik karmaşıklık ana neden — çalışma zamanı ölçümü olmadığı için tek başına kabul edilmedi.",
            ),
            "technical_details": tuple(
                item for item in (
                    f"{getattr(chosen, 'title', '')}: {getattr(chosen, 'explanation', '')}",
                    f"Tekrar sayısı: {getattr(chosen, 'occurrence_count', 0)}; güven: {getattr(chosen, 'confidence', 0.0):.2f}",
                ) if item.strip(": ")
            ),
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
        "hypotheses": (
            "Büyük kaynak dosyası ana neden — çalışma zamanı kanıtı olmadığı için ertelendi.",
            "Bağlam hazırlama darboğazı — ölçüm eklenmeden doğrulanamaz.",
            "Model çağrısı darboğazı — aşama bazlı süre kaydıyla sınanmalı.",
        ),
        "technical_details": (
            "Normal sohbet, kendi kodunu geliştirme ve ses işlemleri ayrı süre alanları olarak ölçülmeli.",
            "Statik karmaşıklık tek başına çalışma zamanı yavaşlığının kanıtı sayılmadı.",
        ),
    }
