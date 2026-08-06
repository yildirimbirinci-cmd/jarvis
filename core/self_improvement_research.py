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
_EXPERIMENT_STATES = {"requested", "accepted", "running", "completed", "failed", "cancelled"}


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


def asks_for_research_experiment_status(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "deney talebi ne durumda",
            "olcum talebi ne durumda",
            "hangi olcumu bekliyorsun",
            "deney gerekiyor mu",
            "ek olcum gerekiyor mu",
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


def asks_about_external_research(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "internet arastirmasi gerekir mi",
            "arastirma icin internet gerekir mi",
            "dis kaynak gerekir mi",
            "internete ihtiyacin var mi",
            "internetten arastirman gerekir mi",
            "hangi kaynaklara dayandin",
            "kanit kaynaklarini goster",
        )
    )


def grants_external_research_permission(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "bu arastirma icin internete izin veriyorum",
            "bu arastirmada internete cikabilirsin",
            "dis kaynak arastirmasina izin veriyorum",
            "internet arastirmasini onayliyorum",
        )
    )


def denies_external_research_permission(text: object) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "bu arastirmada internete cikma",
            "yalnizca kendi loglarini kullan",
            "dis kaynak kullanma",
            "internet arastirmasina izin vermiyorum",
        )
    )


def source_quality_label(url: str) -> str:
    value = _normalize(url)
    if any(marker in value for marker in ("docs.", "documentation", "github.com", "readthedocs", "python.org")):
        return "yüksek"
    if any(marker in value for marker in ("stackoverflow", "issue", "forum", "reddit")):
        return "orta"
    return "belirsiz"




@dataclass(frozen=True, slots=True)
class ResearchExperimentRequest:
    request_id: str
    research_task_id: str
    experiment_type: str
    reason: str
    target: str
    expected_outputs: tuple[str, ...]
    success_criteria: tuple[str, ...]
    safety_constraints: tuple[str, ...]
    created_at: str
    state: str = "requested"
    owner: str = "ExperimentRunner"
    result_reference: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "research_task_id": self.research_task_id,
            "experiment_type": self.experiment_type,
            "reason": self.reason,
            "target": self.target,
            "expected_outputs": list(self.expected_outputs),
            "success_criteria": list(self.success_criteria),
            "safety_constraints": list(self.safety_constraints),
            "created_at": self.created_at,
            "state": self.state,
            "owner": self.owner,
            "result_reference": self.result_reference,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ResearchExperimentRequest":
        state = str(payload.get("state", "requested"))
        if state not in _EXPERIMENT_STATES:
            raise ValueError("invalid experiment request state")
        request = cls(
            request_id=str(payload.get("request_id", "")),
            research_task_id=str(payload.get("research_task_id", "")),
            experiment_type=str(payload.get("experiment_type", "")),
            reason=str(payload.get("reason", "")),
            target=str(payload.get("target", "")),
            expected_outputs=tuple(str(item) for item in payload.get("expected_outputs", ()) if item),
            success_criteria=tuple(str(item) for item in payload.get("success_criteria", ()) if item),
            safety_constraints=tuple(str(item) for item in payload.get("safety_constraints", ()) if item),
            created_at=str(payload.get("created_at", "")),
            state=state,
            owner=str(payload.get("owner", "ExperimentRunner")),
            result_reference=str(payload.get("result_reference", "")),
            error=str(payload.get("error", "")),
        )
        if not request.request_id or not request.research_task_id or not request.experiment_type:
            raise ValueError("incomplete experiment request")
        return request


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
    notification_state: str = "none"
    notification_id: str = ""
    notified_at: str = ""
    failure_kind: str = ""
    next_step: str = ""
    local_runtime_evidence: tuple[str, ...] = ()
    local_code_evidence: tuple[str, ...] = ()
    external_evidence: tuple[str, ...] = ()
    external_sources: tuple[str, ...] = ()
    source_quality_notes: tuple[str, ...] = ()
    evidence_conflicts: tuple[str, ...] = ()
    external_research_state: str = "not_needed"
    external_research_reason: str = ""
    experiment_request_id: str = ""
    experiment_request_state: str = ""
    experiment_request_reason: str = ""
    experiment_result_reference: str = ""
    automation_state: str = "not_started"
    automation_summary: str = ""
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
        payload["local_runtime_evidence"] = list(self.local_runtime_evidence)
        payload["local_code_evidence"] = list(self.local_code_evidence)
        payload["external_evidence"] = list(self.external_evidence)
        payload["external_sources"] = list(self.external_sources)
        payload["source_quality_notes"] = list(self.source_quality_notes)
        payload["evidence_conflicts"] = list(self.evidence_conflicts)
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
            notification_state=str(payload.get("notification_state", "none")),
            notification_id=str(payload.get("notification_id", "")),
            notified_at=str(payload.get("notified_at", "")),
            failure_kind=str(payload.get("failure_kind", "")),
            next_step=str(payload.get("next_step", "")),
            local_runtime_evidence=tuple(str(item) for item in payload.get("local_runtime_evidence", ()) if item),
            local_code_evidence=tuple(str(item) for item in payload.get("local_code_evidence", ()) if item),
            external_evidence=tuple(str(item) for item in payload.get("external_evidence", ()) if item),
            external_sources=tuple(str(item) for item in payload.get("external_sources", ()) if item),
            source_quality_notes=tuple(str(item) for item in payload.get("source_quality_notes", ()) if item),
            evidence_conflicts=tuple(str(item) for item in payload.get("evidence_conflicts", ()) if item),
            external_research_state=str(payload.get("external_research_state", "not_needed")),
            external_research_reason=str(payload.get("external_research_reason", "")),
            experiment_request_id=str(payload.get("experiment_request_id", "")),
            experiment_request_state=str(payload.get("experiment_request_state", "")),
            experiment_request_reason=str(payload.get("experiment_request_reason", "")),
            experiment_result_reference=str(payload.get("experiment_result_reference", "")),
            automation_state=str(payload.get("automation_state", "not_started")),
            automation_summary=str(payload.get("automation_summary", "")),
            error=str(payload.get("error", "")),
        )

    def status_report(self) -> str:
        if self.state == "solution_found":
            return "Araştırma tamamlandı. Sonucu dinlemek için 'ne buldun' diyebilirsin."
        if self.state == "failed":
            return "Araştırma tamamlanamadı. Ayrıntı için 'ne buldun' diyebilirsin."
        if self.state == "cancelled":
            return "Araştırma durduruldu. Yeniden başlatmamı istersen 'baştan araştır' diyebilirsin."
        if self.experiment_request_state in {"requested", "accepted", "running"}:
            return (
                "Araştırma için ek ölçüm gerekiyor. "
                f"Deney talebi {self.experiment_request_id or 'oluşturuldu'} durumunda: "
                f"{self.experiment_request_state}. Research Engine deneyi çalıştırmıyor; "
                "sonucu Experiment Runner'dan bekliyor. Henüz kodumu değiştirmedim."
            )
        message = self.status_message or "Yavaşlığın hangi aşamada oluştuğunu inceliyorum."
        return f"Araştırma devam ediyor. %{self.progress}: {message} Henüz kodumu değiştirmedim."

    def user_report(self) -> str:
        if self.state in {"queued", "researching"}:
            return self.status_report()
        if self.state == "cancelled":
            return "Araştırmayı kullanıcı isteğiyle durdurdum. Hiçbir dosyayı değiştirmedim."
        if self.state == "failed":
            reason = self.error or "Yeterli ve güvenilir kanıt toplanamadı."
            next_step = self.next_step or "Daha dar kapsamlı ölçüm veya ek çalışma zamanı kaydı gerekiyor."
            return (
                "Araştırmayı güvenilir bir sonuca ulaştıramadım. "
                f"Neden: {reason} "
                f"Sonraki güvenli adım: {next_step} "
                "Bir kök neden uydurmadım ve kodumu değiştirmedim."
            )
        automation = self.automation_summary.strip()
        if automation:
            continuation = (
                "\n\nBakım zinciri sonucu: "
                + automation
            )
        elif self.automation_state == "running":
            continuation = (
                "\n\nAraştırma sonucu otomatik bakım zincirine aktarıldı; "
                "planlama ve güvenli doğrulama devam ediyor."
            )
        else:
            continuation = (
                "\n\nAraştırma tamamlandı ancak otomatik bakım sonucu henüz kaydedilmedi."
            )
        return (
            "Araştırmayı tamamladım.\n\n"
            f"Ne buldum: {self.summary}\n"
            f"Bunun anlamı: {self.cause}\n"
            f"Araştırmanın işaret ettiği yaklaşım: {self.solution}\n"
            f"Olası fayda: {self.benefit}\n"
            f"Belirsizlik veya risk: {self.risk}"
            + continuation
            + "\n\nBu aşamada çözüm seçmedim, plan veya patch üretmedim ve henüz hiçbir dosyayı değiştirmedim."
            + "\n\nTeknik kayıtları görmek için 'teknik ayrıntıları göster' de."
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
        runtime = "\n".join(f"- {item}" for item in self.local_runtime_evidence) or "- yok"
        code = "\n".join(f"- {item}" for item in self.local_code_evidence) or "- yok"
        external = "\n".join(f"- {item}" for item in self.external_evidence) or "- kullanılmadı"
        conflicts = "\n".join(f"- {item}" for item in self.evidence_conflicts) or "- çelişki kaydedilmedi"
        quality = "\n".join(f"- {item}" for item in self.source_quality_notes) or "- dış kaynak yok"
        return (
            "TEKNİK ARAŞTIRMA AYRINTILARI\n\n"
            f"İlgili kapsam: {paths}\n"
            f"Doğrulama: {checks}\n"
            f"Kanıt kimlikleri: {', '.join(self.evidence_ids) or 'yok'}\n\n"
            f"YEREL ÇALIŞMA ZAMANI KANITI:\n{runtime}\n\n"
            f"YEREL KOD KANITI:\n{code}\n\n"
            f"DIŞ KAYNAK BULGULARI:\n{external}\n\n"
            f"KAYNAK KALİTESİ:\n{quality}\n\n"
            f"ÇELİŞKİLER:\n{conflicts}\n\n"
            f"Dış araştırma durumu: {self.external_research_state}. "
            f"{self.external_research_reason}\n\n"
            f"Diğer kayıtlar:\n{details}\n\n"
            "Dış kaynaklar yerel kanıt yerine geçmez. Bu rapor yalnızca tanı içindir; kod değişikliği uygulanmadı."
        )



class SelfImprovementResearchStore:
    MAX_BYTES = 256 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.history_path = self.path.with_name(self.path.stem + "_history.json")
        self.tasks_path = self.path.with_name(self.path.stem + "_tasks.json")
        self.experiments_path = self.path.with_name(self.path.stem + "_experiment_requests.json")
        self.experience_store = None
        self._lock = threading.RLock()

    def _load_tasks(self) -> list[SelfImprovementResearchTask]:
        if not self.tasks_path.exists():
            current = self._load_current_only()
            return [current] if current is not None else []
        try:
            if self.tasks_path.stat().st_size > self.MAX_BYTES * 4:
                return []
            payload = json.loads(self.tasks_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return []
            tasks: list[SelfImprovementResearchTask] = []
            for item in payload[-50:]:
                if not isinstance(item, dict):
                    continue
                try:
                    task = SelfImprovementResearchTask.from_dict(item)
                except (ValueError, TypeError):
                    continue
                if task.task_id and task.created_at:
                    tasks.append(task)
            return tasks
        except (OSError, ValueError, TypeError, UnicodeError):
            return []

    def _save_tasks(self, tasks: Iterable[SelfImprovementResearchTask]) -> None:
        deduplicated: dict[str, SelfImprovementResearchTask] = {}
        for task in tasks:
            if task.task_id:
                deduplicated[task.task_id] = task
        ordered = sorted(deduplicated.values(), key=lambda item: item.created_at)[-50:]
        encoded = json.dumps(
            [task.to_dict() for task in ordered],
            ensure_ascii=False, indent=2, allow_nan=False,
        ).encode("utf-8")
        self.tasks_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.tasks_path.parent,
                prefix=f".{self.tasks_path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                handle.write(encoded)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.tasks_path)
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def _load_current_only(self) -> SelfImprovementResearchTask | None:
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

    def list_tasks(self, *, states: Iterable[str] | None = None) -> tuple[SelfImprovementResearchTask, ...]:
        allowed = set(states or ())
        tasks = self._load_tasks()
        if allowed:
            tasks = [task for task in tasks if task.state in allowed]
        return tuple(sorted(tasks, key=lambda item: item.created_at))

    def active_tasks(self) -> tuple[SelfImprovementResearchTask, ...]:
        return self.list_tasks(states=("queued", "researching"))

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

    def load(self, task_id: str | None = None) -> SelfImprovementResearchTask | None:
        if task_id:
            for task in self._load_tasks():
                if task.task_id == task_id:
                    return task
            current = self._load_current_only()
            return current if current is not None and current.task_id == task_id else None
        return self._load_current_only()

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
            tasks = [item for item in self._load_tasks() if item.task_id != task.task_id]
            tasks.append(task)
            self._save_tasks(tasks)

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
            local_runtime_evidence=tuple(dict.fromkeys(str(item) for item in evidence_ids if item))[:20],
            local_code_evidence=tuple(dict.fromkeys(str(item) for item in affected_paths if item))[:20],
            external_research_state=(
                "recommended" if not tuple(str(item) for item in evidence_ids if item) else "not_needed"
            ),
            external_research_reason=(
                "Yerel çalışma zamanı kanıtı kök nedeni doğrulamaya yetmedi; dış kaynak yalnızca karşılaştırma için kullanılabilir."
                if not tuple(str(item) for item in evidence_ids if item) else
                "Yerel çalışma zamanı kanıtı mevcut; dış kaynak araştırması zorunlu değil."
            ),
            journal_entries=task.journal_entries + (f"{_now()} — Araştırma tamamlandı; bulgular ve belirsizlikler kaydedildi.",),
            notification_state="pending",
            notification_id="",
            notified_at="",
            failure_kind="",
            next_step="",
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

    def set_external_research_permission(
        self, task: SelfImprovementResearchTask, *, allowed: bool
    ) -> SelfImprovementResearchTask:
        state = "approved" if allowed else "denied"
        reason = (
            "Kullanıcı bu araştırma için dış kaynak kullanımına açık izin verdi."
            if allowed else
            "Kullanıcı bu araştırmada yalnızca yerel kanıt kullanılmasını istedi."
        )
        updated = replace(
            task,
            external_research_state=state,
            external_research_reason=reason,
            journal_entries=task.journal_entries + (f"{_now()} — Dış araştırma izni: {state}.",),
        )
        self.save(updated)
        return updated

    def record_external_evidence(
        self,
        task: SelfImprovementResearchTask,
        *,
        findings: Iterable[str],
        sources: Iterable[str],
        conflicts: Iterable[str] = (),
    ) -> SelfImprovementResearchTask:
        if task.external_research_state != "approved":
            raise PermissionError("external research requires task-specific permission")
        source_items = tuple(dict.fromkeys(str(item) for item in sources if item))[:12]
        finding_items = tuple(dict.fromkeys(str(item) for item in findings if item))[:20]
        conflict_items = tuple(dict.fromkeys(str(item) for item in conflicts if item))[:12]
        quality = tuple(f"{item} — kalite: {source_quality_label(item)}" for item in source_items)
        updated = replace(
            task,
            external_evidence=finding_items,
            external_sources=source_items,
            source_quality_notes=quality,
            evidence_conflicts=conflict_items,
            external_research_state="completed",
            external_research_reason=(
                "Dış kaynak bulguları yerel kanıttan ayrı kaydedildi ve yalnızca karşılaştırma amacıyla kullanıldı."
            ),
            journal_entries=task.journal_entries + (f"{_now()} — Dış kaynak karşılaştırması tamamlandı.",),
        )
        self.save(updated)
        return updated

    def external_research_report(self, task: SelfImprovementResearchTask) -> str:
        state = task.external_research_state
        if state == "recommended":
            return (
                "Yerel kanıt güvenilir bir sonuca yetmediği için dış kaynak karşılaştırması yararlı olabilir. "
                "İzin verirsen yalnızca bu araştırma için kullanacağım; dış kaynaklar yerel kanıt yerine geçmeyecek."
            )
        if state == "approved":
            return "Bu araştırma için dış kaynak izni verildi; karşılaştırma henüz tamamlanmadı."
        if state == "denied":
            return "Bu araştırmada dış kaynak kullanılmayacak; yalnızca yerel kanıtla devam edeceğim."
        if state == "completed":
            return (
                f"Dış kaynak karşılaştırması tamamlandı. {len(task.external_sources)} kaynak ve "
                f"{len(task.evidence_conflicts)} çelişki kaydedildi. Teknik ayrıntılarda ayrı gösteriliyor."
            )
        return "Yerel kanıt yeterli göründüğü için bu araştırmada dış kaynak zorunlu değil."

    def _load_experiment_requests(self) -> list[ResearchExperimentRequest]:
        if not self.experiments_path.exists():
            return []
        try:
            if self.experiments_path.stat().st_size > self.MAX_BYTES * 4:
                return []
            payload = json.loads(self.experiments_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return []
            requests: list[ResearchExperimentRequest] = []
            for item in payload[-100:]:
                if not isinstance(item, dict):
                    continue
                try:
                    requests.append(ResearchExperimentRequest.from_dict(item))
                except (ValueError, TypeError):
                    continue
            return requests
        except (OSError, ValueError, TypeError, UnicodeError):
            return []

    def _save_experiment_requests(self, requests: Iterable[ResearchExperimentRequest]) -> None:
        deduplicated = {item.request_id: item for item in requests if item.request_id}
        ordered = sorted(deduplicated.values(), key=lambda item: item.created_at)[-100:]
        encoded = json.dumps(
            [item.to_dict() for item in ordered], ensure_ascii=False, indent=2, allow_nan=False
        ).encode("utf-8")
        self.experiments_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.experiments_path.parent,
                prefix=f".{self.experiments_path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                handle.write(encoded)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.experiments_path)
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def list_experiment_requests(
        self, *, research_task_id: str | None = None
    ) -> tuple[ResearchExperimentRequest, ...]:
        requests = self._load_experiment_requests()
        if research_task_id:
            requests = [item for item in requests if item.research_task_id == research_task_id]
        return tuple(requests)

    def request_measurement_experiment(
        self,
        task: SelfImprovementResearchTask,
        *,
        reason: str,
        target: str,
        expected_outputs: Iterable[str],
        success_criteria: Iterable[str],
        safety_constraints: Iterable[str] = (),
        experiment_type: str = "latency_measurement",
    ) -> tuple[SelfImprovementResearchTask, ResearchExperimentRequest]:
        if task.state in {"cancelled", "failed"}:
            raise ValueError("inactive research cannot request an experiment")
        normalized_type = str(experiment_type or "").strip()[:120]
        normalized_target = str(target or "").strip()[:500]
        normalized_reason = " ".join(str(reason or "").split())[:2000]
        if not normalized_type or not normalized_target or not normalized_reason:
            raise ValueError("experiment request requires type, target and reason")
        outputs = tuple(dict.fromkeys(str(item) for item in expected_outputs if item))[:20]
        criteria = tuple(dict.fromkeys(str(item) for item in success_criteria if item))[:20]
        constraints = tuple(dict.fromkeys(str(item) for item in safety_constraints if item))[:20]
        if not outputs or not criteria:
            raise ValueError("experiment request requires outputs and success criteria")
        existing = [
            item for item in self._load_experiment_requests()
            if item.research_task_id == task.task_id
            and item.experiment_type == normalized_type
            and _normalize(item.target) == _normalize(normalized_target)
            and item.state not in {"failed", "cancelled"}
        ]
        if existing:
            request = existing[-1]
        else:
            request = ResearchExperimentRequest(
                request_id="EXP-" + uuid.uuid4().hex[:10].upper(),
                research_task_id=task.task_id,
                experiment_type=normalized_type,
                reason=normalized_reason,
                target=normalized_target,
                expected_outputs=outputs,
                success_criteria=criteria,
                safety_constraints=constraints or (
                    "Gerçek kaynak dosyalarını değiştirme.",
                    "Ölçümü izole veya salt-okunur çalıştır.",
                    "Sonucu araştırma kimliğiyle ilişkilendir.",
                ),
                created_at=_now(),
            )
            requests = self._load_experiment_requests()
            requests.append(request)
            self._save_experiment_requests(requests)
        updated = replace(
            task,
            state="researching",
            stage="experiment_required",
            progress=min(max(task.progress, 70), 95),
            status_message="Araştırmayı tamamlamak için ölçüm sonucu bekleniyor.",
            experiment_request_id=request.request_id,
            experiment_request_state=request.state,
            experiment_request_reason=request.reason,
            journal_entries=task.journal_entries + (
                f"{_now()} — Deney talebi oluşturuldu: {request.request_id} ({request.experiment_type}).",
            ),
        )
        self.save(updated)
        return updated, request

    def update_experiment_request(
        self, request: ResearchExperimentRequest, *, state: str,
        result_reference: str = "", error: str = "",
    ) -> ResearchExperimentRequest:
        if state not in _EXPERIMENT_STATES:
            raise ValueError("invalid experiment request state")
        updated = replace(
            request, state=state, result_reference=str(result_reference or "")[:1000],
            error=str(error or "")[:2000],
        )
        requests = [item for item in self._load_experiment_requests() if item.request_id != request.request_id]
        requests.append(updated)
        self._save_experiment_requests(requests)
        task = self.load(request.research_task_id)
        if task is not None:
            task_update = replace(
                task,
                experiment_request_state=state,
                experiment_result_reference=updated.result_reference,
                stage=("experiment_result_ready" if state == "completed" else task.stage),
                status_message=(
                    "Ölçüm sonucu araştırma değerlendirmesi için hazır."
                    if state == "completed" else task.status_message
                ),
                journal_entries=task.journal_entries + (
                    f"{_now()} — Deney talebi {request.request_id}: {state}.",
                ),
            )
            self.save(task_update)
        return updated

    def experiment_request_report(self, task: SelfImprovementResearchTask) -> str:
        if not task.experiment_request_id:
            return "Bu araştırma için deney veya ek ölçüm talebi oluşturulmadı."
        state = task.experiment_request_state or "requested"
        return (
            f"Deney talebi: {task.experiment_request_id}. Durum: {state}. "
            f"Neden: {task.experiment_request_reason or 'ek ölçüm gerekiyor'}. "
            "Talebin sahibi ExperimentRunner'dır; Research Engine deneyi çalıştırmaz, "
            "patch üretmez ve gerçek dosyaları değiştirmez."
        )

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


    def record_automation_result(
        self,
        task: SelfImprovementResearchTask,
        *,
        state: str,
        summary: str,
    ) -> SelfImprovementResearchTask:
        allowed = {
            "running",
            "completed",
            "blocked",
            "failed",
            "inconclusive",
        }
        normalized_state = str(state or "").strip().casefold()
        if normalized_state not in allowed:
            raise ValueError("invalid automation state")
        updated = replace(
            task,
            automation_state=normalized_state,
            automation_summary=" ".join(str(summary or "").split())[:4000],
            journal_entries=task.journal_entries + (
                f"{_now()} — Otomatik bakım zinciri: {normalized_state}.",
            ),
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
        current = self.load(task_id)
        return bool(current is not None and current.state in {"queued", "researching"})

    def fail(
        self,
        task: SelfImprovementResearchTask,
        error: object,
        *,
        failure_kind: str = "execution_error",
        next_step: str = "Araştırmayı daha dar kapsamla yeniden çalıştır ve eksik ölçümü topla.",
    ) -> SelfImprovementResearchTask:
        raw = " ".join(str(error or "").split())
        safe_error = raw[:500] if raw else "Araştırma sırasında beklenmeyen bir teknik sorun oluştu."
        failed = replace(
            task,
            state="failed",
            completed_at=_now(),
            stage="failed",
            progress=100,
            status_message="Araştırma güvenilir bir sonuca ulaştırılamadı.",
            notification_state="pending",
            notification_id="",
            notified_at="",
            failure_kind=str(failure_kind).strip()[:80],
            next_step=" ".join(str(next_step or "").split())[:1000],
            error=safe_error,
            journal_entries=task.journal_entries + (f"{_now()} — Araştırma güvenilir bir sonuca ulaşmadan durdu.",),
        )
        self.save(failed)
        return failed

    def mark_notification_sent(
        self, task: SelfImprovementResearchTask, notification_id: str
    ) -> SelfImprovementResearchTask:
        updated = replace(
            task,
            notification_state="sent",
            notification_id=str(notification_id or "").strip(),
            notified_at=_now(),
        )
        self.save(updated)
        return updated

    def mark_notification_read(self, task: SelfImprovementResearchTask) -> SelfImprovementResearchTask:
        if task.notification_state == "read":
            return task
        updated = replace(task, notification_state="read")
        self.save(updated)
        return updated

    def pending_notifications(self) -> tuple[SelfImprovementResearchTask, ...]:
        return tuple(
            task for task in self.list_tasks(states=("solution_found", "failed"))
            if task.notification_state == "pending"
        )


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
