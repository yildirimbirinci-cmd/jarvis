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
asks_for_self_improvement_status = module.asks_for_self_improvement_status
asks_for_self_improvement_technical_details = module.asks_for_self_improvement_technical_details
SelfImprovementResearchStore = module.SelfImprovementResearchStore
choose_speed_research_result = module.choose_speed_research_result
looks_like_opened_item_followup = module.looks_like_opened_item_followup


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
    assert "Araştırmanın işaret ettiği yaklaşım:" in report
    assert "çözüm seçmedim" in report
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


def test_opened_item_followup_requires_explicit_open_question() -> None:
    assert looks_like_opened_item_followup("Az önce ne açtın?")
    assert looks_like_opened_item_followup("Hangi klasörü açtın?")
    assert not looks_like_opened_item_followup(
        "Önce problemi anlamaya çalış, nedenlerini araştır ve açık onayımı bekle."
    )
    assert not looks_like_opened_item_followup(
        "Üçüncü çözümle devam et; henüz performans davranışını değiştirme."
    )


def test_natural_slowed_down_research_request_is_recognized() -> None:
    assert looks_like_self_improvement_complaint(
        "Son zamanlarda cevap verirken yavaşladığını hissediyorum. "
        "Bunun nedenlerini araştır. Önce problemi anlamaya çalış."
    )


def test_new_task_is_queued_and_has_short_status(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Neden yavaşladığını araştır.")
    assert task.state == "queued"
    assert task.progress == 0
    assert "Araştırma devam ediyor" in task.status_report()
    assert "Dayandığım kanıtlar" not in task.status_report()


def test_progress_and_technical_report_are_separate(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Neden yavaşladığını araştır.")
    task = store.update_progress(
        task,
        stage="runtime_evidence",
        progress=35,
        status_message="Normal sohbet sürelerini inceliyorum.",
    )
    assert "%35" in task.status_report()
    assert "Normal sohbet" in task.status_report()
    completed = store.complete(
        task,
        summary="Görev yürütme aşaması ölçülen en güçlü darboğaz.",
        cause="Bazı görevler kendi süre eşiğini aşıyor.",
        solution="Önce alt aşamalara süre ölçümü ekle.",
        benefit="Yanlış optimizasyon yapmadan gerçek darboğazı bulmak.",
        risk="Düşük.",
        affected_paths=("core/task_orchestrator.py",),
        validation=("Aynı komutu üç kez ölç.",),
        evidence_ids=("RUN-SLOW",),
        technical_details=("TaskOrchestrator.execute_task median=98236ms",),
    )
    plain = completed.user_report()
    technical = completed.technical_report()
    assert "TaskOrchestrator.execute_task" not in plain
    assert "TaskOrchestrator.execute_task" in technical
    assert "teknik ayrıntıları göster" in plain


def test_journal_command_is_recognized() -> None:
    assert module.asks_for_self_improvement_journal("Araştırma günlüğünü göster")
    assert module.asks_for_self_improvement_journal("Hangi hipotezleri denedin?")


def test_research_journal_records_progress_and_hypotheses(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Cevapların yavaş, nedenini araştır.")
    task = store.update_progress(
        task,
        stage="runtime_evidence",
        progress=20,
        status_message="Normal sohbet sürelerini inceliyorum.",
    )
    completed = store.complete(
        task,
        summary="Görev yürütme aşaması en güçlü aday.",
        cause="Bazı görevler süre eşiğini aşıyor.",
        solution="Alt aşamalara süre ölçümü ekle.",
        benefit="Gerçek darboğazı bulmak.",
        risk="Düşük.",
        hypotheses=(
            "Görev yürütme darboğazı — desteklendi.",
            "Ses sistemi ana neden — elendi.",
        ),
    )
    report = completed.journal_report()
    assert "Araştırma görevi oluşturuldu" in report
    assert "%20" in report
    assert "Görev yürütme darboğazı" in report
    assert "Ses sistemi ana neden" in report


def test_completed_research_is_archived_and_reused(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    first = store.start("Cevap verirken çok yavaşlıyorsun, nedenini araştır.")
    first = store.complete(
        first,
        summary="Bağlam hazırlığı yavaş.",
        cause="Gereğinden fazla içerik hazırlanıyor.",
        solution="İlgili sembolleri seç.",
        benefit="Daha kısa yanıt süresi.",
        risk="Düşük.",
    )
    second = store.start("Yine cevap verirken yavaşladın, bunu araştır.")
    assert first.task_id in second.related_research_ids
    assert store.history_path.exists()


def test_plan_request_is_recognized() -> None:
    assert module.asks_for_self_improvement_plan("Bu çözüm için plan hazırla")
    assert module.asks_for_self_improvement_plan("Seçenekleri karşılaştır")


def test_planner_requires_completed_research(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Neden yavaşladığını araştır.")
    try:
        store.prepare_plan(task)
    except ValueError as exc:
        assert "completed" in str(exc)
    else:
        raise AssertionError("incomplete research must not be planned")


def test_planner_compares_options_without_creating_patch(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Neden yavaşladığını araştır.")
    task = store.complete(
        task,
        summary="Görev yürütme aşaması yavaş.",
        cause="Bir alt işlem süre eşiğini aşıyor.",
        solution="Hedefli iyileştirme yap.",
        benefit="Daha kısa yanıt süresi.",
        risk="Düşük-orta.",
        affected_paths=("core/task_orchestrator.py",),
        validation=("Aynı komutu üç kez ölç.",),
        evidence_ids=("RUN-SLOW",),
    )
    planned = store.prepare_plan(task)
    assert len(planned.plan_options) == 3
    assert "2. seçenek" in planned.recommended_option
    report = planned.plan_report()
    assert "KENDİNİ GELİŞTİRME PLANI" in report
    assert "Henüz patch üretmedim" in report
    assert "core/task_orchestrator.py" in report


def test_planner_prefers_measurement_without_runtime_evidence(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Neden yavaşladığını araştır.")
    task = store.complete(
        task, summary="Kanıt yetersiz.", cause="Aşamalar ölçülmedi.",
        solution="Ölçüm ekle.", benefit="Kök nedeni bul.", risk="Düşük."
    )
    planned = store.prepare_plan(task)
    assert "1. seçenek" in planned.recommended_option

REFLECTION_MODULE_PATH = Path(__file__).parents[1] / "core" / "self_reflection_engine.py"
reflection_spec = importlib.util.spec_from_file_location(
    "self_reflection_engine", REFLECTION_MODULE_PATH
)
assert reflection_spec is not None and reflection_spec.loader is not None
reflection_module = importlib.util.module_from_spec(reflection_spec)
sys.modules[reflection_spec.name] = reflection_module
reflection_spec.loader.exec_module(reflection_module)
classify_self_feedback = reflection_module.classify_self_feedback
natural_research_start_message = reflection_module.natural_research_start_message


def test_natural_feedback_is_classified_without_research_command() -> None:
    cases = {
        "Bugün biraz yavaşsın.": "performance",
        "Kendini çok tekrar ediyorsun.": "repetition",
        "Bazen konuyu kaçırıyorsun.": "context",
        "Eskisi kadar doğal konuşmuyorsun.": "dialogue_quality",
        "Konuşurken bazen takılıyorsun.": "voice_stability",
    }
    for text, category in cases.items():
        feedback = classify_self_feedback(text)
        assert feedback is not None
        assert feedback.category == category


def test_external_slow_system_is_not_self_feedback() -> None:
    assert classify_self_feedback("İnternetim bugün çok yavaş.") is None
    assert classify_self_feedback("Bilgisayarım ağır çalışıyor.") is None


def test_reflection_response_is_natural_and_safe() -> None:
    feedback = classify_self_feedback("Bugün biraz yavaşsın.")
    assert feedback is not None
    message = natural_research_start_message(feedback)
    assert "araştıracağım" in message
    assert "kodumu değiştirmeyeceğim" in message
    assert "cyclomatic" not in message
    assert "self improvement" not in message.casefold()


def test_store_persists_feedback_category(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start(
        "Kendini çok tekrar ediyorsun.",
        feedback_category="repetition",
        reflection_confidence=0.9,
    )
    loaded = store.load()
    assert loaded == task
    assert loaded.feedback_category == "repetition"
    assert loaded.reflection_confidence == 0.9

choose_reflection_research_result = reflection_module.choose_reflection_research_result


def test_non_performance_feedback_gets_category_specific_research() -> None:
    result = choose_reflection_research_result(
        "context", RuntimeReport(()), ArchitectureAssessment(),
        speed_result_factory=choose_speed_research_result,
    )
    assert "Bağlam" in result["summary"]
    assert "conversation_context" in " ".join(result["affected_paths"])
    assert "yavaşlığın" not in result["summary"].casefold()

EXPERIENCE_MODULE_PATH = Path(__file__).parents[1] / "core" / "self_improvement_experience.py"
experience_spec = importlib.util.spec_from_file_location(
    "self_improvement_experience", EXPERIENCE_MODULE_PATH
)
assert experience_spec is not None and experience_spec.loader is not None
experience_module = importlib.util.module_from_spec(experience_spec)
sys.modules[experience_spec.name] = experience_module
experience_spec.loader.exec_module(experience_module)
SelfImprovementExperienceStore = experience_module.SelfImprovementExperienceStore
parse_experience_outcome = experience_module.parse_experience_outcome
asks_for_experience_report = experience_module.asks_for_experience_report


def test_completed_research_becomes_experience(tmp_path: Path) -> None:
    research = SelfImprovementResearchStore(tmp_path / "research.json")
    experiences = SelfImprovementExperienceStore(tmp_path / "experiences.json")
    research.experience_store = experiences
    task = research.start("Bugün biraz yavaşsın.", feedback_category="performance")
    research.complete(
        task,
        summary="Bağlam hazırlığı yavaş.",
        cause="Gereğinden fazla içerik hazırlanıyor.",
        solution="İlgili sembolleri seç.",
        benefit="Daha kısa yanıt süresi.",
        risk="Düşük.",
    )
    records = experiences.load_all()
    assert len(records) == 1
    assert records[0].research_id == task.task_id
    assert records[0].outcome == "untested"


def test_successful_experience_is_reused_in_new_research(tmp_path: Path) -> None:
    research = SelfImprovementResearchStore(tmp_path / "research.json")
    experiences = SelfImprovementExperienceStore(tmp_path / "experiences.json")
    research.experience_store = experiences
    first = research.start("Cevap verirken çok yavaşsın.", feedback_category="performance")
    first = research.complete(
        first,
        summary="Bağlam hazırlığı yavaş.", cause="Fazla içerik hazırlanıyor.",
        solution="İlgili sembolleri seç.", benefit="Hızlanma.", risk="Düşük.",
    )
    recorded = experiences.record_outcome(first.task_id, "successful", "Artık daha hızlı.")
    assert recorded is not None
    second = research.start("Bugün yine cevap verirken yavaşsın.", feedback_category="performance")
    assert second.related_experience_ids
    assert "işe yaramıştı" in second.experience_context


def test_failed_experience_is_not_blindly_recommended(tmp_path: Path) -> None:
    research = SelfImprovementResearchStore(tmp_path / "research.json")
    experiences = SelfImprovementExperienceStore(tmp_path / "experiences.json")
    research.experience_store = experiences
    first = research.start("Konuyu kaçırıyorsun.", feedback_category="context")
    first = research.complete(
        first, summary="Bağlam budanıyor.", cause="Erken budama.",
        solution="Daha fazla geçmiş taşı.", benefit="Bağlam.", risk="Orta.",
    )
    experiences.record_outcome(first.task_id, "failed", "Daha da yavaşladı.")
    second = research.start("Yine konuyu kaçırdın.", feedback_category="context")
    assert "körü körüne tekrarlamayacağım" in second.experience_context


def test_experience_outcome_language_is_recognized() -> None:
    assert parse_experience_outcome("Bu çözüm işe yaradı")[0] == "successful"
    assert parse_experience_outcome("Biraz düzeldi ama tam değil")[0] == "partial"
    assert parse_experience_outcome("Hiç işe yaramadı")[0] == "failed"
    assert asks_for_experience_report("Daha önce ne öğrendin?")


def test_cancel_and_restart_commands_are_recognized() -> None:
    assert module.asks_to_cancel_self_improvement_research("Araştırmayı durdur")
    assert module.asks_to_cancel_self_improvement_research("Bunu araştırmayı bırak")
    assert module.asks_to_restart_self_improvement_research("Araştırmayı baştan başlat")
    assert module.asks_to_restart_self_improvement_research("Yeniden araştır")


def test_store_cancels_active_research_without_overwriting_result(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Neden yavaşladığını araştır.")
    cancelled = store.cancel(task)
    assert cancelled.state == "cancelled"
    assert not store.is_active(task.task_id)
    assert "durdur" in cancelled.status_report().casefold()
    assert "Hiçbir dosyayı değiştirmedim" in cancelled.user_report()


def test_cancel_is_idempotent_for_completed_research(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Neden yavaşladığını araştır.")
    completed = store.complete(
        task, summary="Ölçüm gerekli.", cause="Kanıt yetersiz.",
        solution="Aşama sürelerini ölç.", benefit="Doğru kök neden.", risk="Düşük."
    )
    assert store.cancel(completed) == completed


def test_duplicate_runtime_findings_are_collapsed() -> None:
    @dataclass
    class DuplicateFinding:
        category: str = "repeated_slow_operation"
        occurrence_count: int = 4
        confidence: float = 0.8
        affected_paths: tuple[str, ...] = ("core/task_orchestrator.py",)
        affected_symbols: tuple[str, ...] = ("TaskOrchestrator.execute_task",)
        title: str = "slow"
        explanation: str = "threshold exceeded"
        finding_id: str = "DUP"

    report = RuntimeReport((DuplicateFinding(), DuplicateFinding(occurrence_count=9, confidence=0.95)))
    result = choose_speed_research_result(report, ArchitectureAssessment())
    assert result["evidence_ids"] == ("DUP",)
    assert any("Tekrar sayısı: 9" in item for item in result["technical_details"])
    assert not any("Tekrar sayısı: 4" in item for item in result["technical_details"])


def test_multiple_feedback_categories_are_extracted_from_one_message() -> None:
    feedbacks = reflection_module.classify_self_feedback_many(
        "Aynı anda iki araştırma başlat. Birincisi performansın. "
        "İkincisi konuşma kaliten."
    )
    assert [item.category for item in feedbacks] == ["performance", "dialogue_quality"]


def test_store_keeps_multiple_research_tasks_separate(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    performance = store.start(
        "Performansını araştır.", feedback_category="performance", reflection_confidence=0.9
    )
    dialogue = store.start(
        "Konuşma kaliteni araştır.", feedback_category="dialogue_quality", reflection_confidence=0.9
    )
    assert performance.task_id != dialogue.task_id
    assert store.load(performance.task_id) == performance
    assert store.load(dialogue.task_id) == dialogue
    assert {task.feedback_category for task in store.active_tasks()} == {
        "performance", "dialogue_quality"
    }


def test_progress_of_one_research_does_not_overwrite_the_other(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    performance = store.start("Performansını araştır.", feedback_category="performance")
    dialogue = store.start("Konuşma kaliteni araştır.", feedback_category="dialogue_quality")
    updated = store.update_progress(
        performance, stage="runtime", progress=55, status_message="Performansı ölçüyorum."
    )
    assert store.load(updated.task_id).progress == 55
    untouched = store.load(dialogue.task_id)
    assert untouched is not None
    assert untouched.progress == 0
    assert untouched.feedback_category == "dialogue_quality"


def test_research_report_does_not_claim_solution_selection_or_plan(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Konuşma kaliteni araştır.", feedback_category="dialogue_quality")
    completed = store.complete(
        task,
        summary="Teknik ayrıntılar ana cevaba karışıyor.",
        cause="Sunum katmanları ayrılmamış olabilir.",
        solution="Günlük dil ve teknik ayrıntıyı ayrı sunmak değerlendirilebilir.",
        benefit="Daha anlaşılır cevaplar.",
        risk="Aşırı sadeleştirme.",
    )
    report = completed.user_report()
    assert "çözüm seçmedim" in report
    assert "plan veya patch üretmedim" in report
    assert "Seçtiğimiz çözüm" not in report
    assert "Uygulama yaklaşımı" not in report


def test_completed_research_notification_lifecycle_is_persistent(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Cevapların yavaş, nedenini araştır.")
    completed = store.complete(
        task,
        summary="Ölçüm gerekli.",
        cause="Kök neden henüz kanıtlanmadı.",
        solution="Aşama sürelerini ölç.",
        benefit="Yanlış optimizasyonu önler.",
        risk="Düşük.",
    )
    assert completed.notification_state == "pending"
    assert store.pending_notifications() == (completed,)

    sent = store.mark_notification_sent(completed, "NOTIFY-1")
    assert sent.notification_state == "sent"
    assert sent.notification_id == "NOTIFY-1"
    assert store.pending_notifications() == ()

    read = store.mark_notification_read(sent)
    assert read.notification_state == "read"
    assert store.load(read.task_id) == read


def test_legacy_completed_research_does_not_create_duplicate_notification(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Eski araştırma")
    legacy = module.replace(
        task,
        state="solution_found",
        completed_at=module._now(),
        notification_state="none",
    )
    store.save(legacy)
    assert store.pending_notifications() == ()


def test_failed_research_reports_uncertainty_and_next_step(tmp_path: Path) -> None:
    store = SelfImprovementResearchStore(tmp_path / "research.json")
    task = store.start("Konuşma kalitesini araştır.", feedback_category="dialogue_quality")
    failed = store.fail(
        task,
        RuntimeError("ölçüm kaydı okunamadı"),
        failure_kind="insufficient_evidence",
        next_step="Konuşma örneklerini ayrı oturumlarda ölç.",
    )
    report = failed.user_report()
    assert failed.notification_state == "pending"
    assert failed.failure_kind == "insufficient_evidence"
    assert "Bir kök neden uydurmadım" in report
    assert "Konuşma örneklerini" in report
    assert "kodumu değiştirmedim" in report
