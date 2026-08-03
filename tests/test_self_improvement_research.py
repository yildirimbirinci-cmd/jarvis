from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "core" / "self_improvement_research.py"
spec = importlib.util.spec_from_file_location("self_improvement_research", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


looks_like_self_improvement_complaint = module.looks_like_self_improvement_complaint
asks_for_self_improvement_result = module.asks_for_self_improvement_result
SelfImprovementResearchStore = module.SelfImprovementResearchStore
choose_speed_research_result = module.choose_speed_research_result


def test_natural_speed_complaint_is_recognized() -> None:
    assert looks_like_self_improvement_complaint(
        "Çok yavaş düşünüyorsun. Bu konuda kendini geliştirmek için ne yapabilirsin?"
    )
    assert looks_like_self_improvement_complaint(
        "Cevap vermen uzun sürüyor, bunu araştırıp çözüm bul."
    )


def test_plain_maintenance_or_unrelated_speed_text_is_not_recognized() -> None:
    assert not looks_like_self_improvement_complaint("Bakım raporunu göster")
    assert not looks_like_self_improvement_complaint("İnternetim çok yavaş")
    assert not looks_like_self_improvement_complaint("Daha hızlı cevap ver")


def test_result_follow_up_is_recognized() -> None:
    assert asks_for_self_improvement_result("Ne buldun?")
    assert asks_for_self_improvement_result("Yavaşlık araştırması sonucu nedir?")


def test_store_round_trip_and_plain_language_report(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Çok yavaş düşünüyorsun, çözüm bul.")
    completed = store.complete(
        task,
        summary="Model çağrısı tekrar tekrar kendi süre eşiğini aşıyor.",
        cause="Bağlam hazırlığı gereğinden fazla içerik taşıyor.",
        solution="Yalnızca ilgili sembolleri modele gönder.",
        benefit="Daha kısa cevap süresi.",
        risk="Eksik bağlam seçilirse cevap kalitesi düşebilir.",
        affected_paths=("core/assistant.py",),
        validation=("Aynı isteği üç kez ölç.",),
        evidence_ids=("RUN-123",),
    )
    loaded = store.load()
    assert loaded == completed
    report = loaded.user_report()
    assert "Ne buldum:" in report
    assert "Önerdiğim çözüm:" in report
    assert "henüz hiçbir dosyayı değiştirmedim" in report
    assert "cyclomatic" not in report


@dataclass
class Evidence:
    duration_ms: float


@dataclass
class RuntimeFinding:
    finding_id: str = "RUN-SLOW"
    category: str = "repeated_slow_operation"
    title: str = "Tekrarlanan yavaş işlem: LocalDialogueManager.respond"
    explanation: str = "İşlem 5 kez eşiği aştı; ortanca süre 8200 ms."
    occurrence_count: int = 5
    confidence: float = 0.92
    affected_paths: tuple[str, ...] = ("core/local_dialogue.py",)
    affected_symbols: tuple[str, ...] = ("LocalDialogueManager.respond",)
    acceptance_criteria: tuple[str, ...] = ("Ortanca süre düşmeli.",)
    evidence: tuple[Evidence, ...] = (Evidence(8200.0),)


@dataclass
class RuntimeReport:
    findings: tuple[RuntimeFinding, ...]


@dataclass
class ArchitectureAssessment:
    findings: tuple[object, ...] = ()


def test_runtime_latency_evidence_is_preferred_over_static_complexity() -> None:
    result = choose_speed_research_result(
        RuntimeReport((RuntimeFinding(),)), ArchitectureAssessment()
    )
    assert "ölçülmüş" in result["summary"]
    assert result["affected_paths"] == ("core/local_dialogue.py",)
    assert result["evidence_ids"] == ("RUN-SLOW",)


def test_no_runtime_evidence_proposes_measurement_not_fake_root_cause() -> None:
    result = choose_speed_research_result(RuntimeReport(()), ArchitectureAssessment())
    assert "henüz kanıtlamıyor" in result["summary"]
    assert "süre ölçümü" in result["solution"]
    assert "core/assistant.py" in result["affected_paths"]
