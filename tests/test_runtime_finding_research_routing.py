from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import (
    AssistantEngine,
)


def _finding():
    evidence = (
        SimpleNamespace(
            source_path="core/task_orchestrator.py",
            symbol="TaskOrchestrator.wrap.execute",
            detail=(
                "TaskOrchestrator.execute_task "
                "durumu: completed"
            ),
            duration_ms=40542.0,
        ),
    )

    return SimpleNamespace(
        finding_id="RUN-06578E9EDE",
        severity="medium",
        category="repeated_slow_operation",
        title=(
            "Tekrarlanan yavas islem: "
            "TaskOrchestrator.execute_task"
        ),
        explanation=(
            "Islem 31 kez kendi 30000 ms "
            "esigini asti."
        ),
        recommendation=(
            "Aktif calisma ve bekleme surelerini "
            "ayri olc."
        ),
        occurrence_count=31,
        confidence=0.96,
        source_path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
        evidence=evidence,
        acceptance_criteria=(
            "Ortanca sure olculebilir bicimde dusmeli.",
            "Cikti ve davranis korunmali.",
            "Iptal ve ilerleme bildirimi korunmali.",
        ),
        affected_paths=(
            "core/task_orchestrator.py",
        ),
        affected_symbols=(
            "TaskOrchestrator.wrap.execute",
        ),
        workspace=".",
        scope="own_code",
    )


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(
        AssistantEngine
    )
    engine._find_runtime_finding = (
        lambda _finding_id: _finding()
    )
    return engine


def test_reserved_run_research_returns_local_plan() -> None:
    engine = _engine()

    rendered = engine._reserved_self_repair_request(
        "RUN-06578E9EDE bulgusunu arastir. "
        "Hicbir kodu degistirme."
    )

    assert rendered is not None
    assert "KANITA DAYALI ARASTIRMA PLANI" in rendered
    assert "Durum: LOCAL_REVIEW" in rendered
    assert "core/task_orchestrator.py" in rendered
    assert "TaskOrchestrator.wrap.execute" in rendered
    assert "Internet arastirmasi baslatilmadi" in rendered
    assert "hicbir kaynak dosya degistirilmedi" in rendered


def test_root_cause_wording_is_research_intent() -> None:
    engine = _engine()

    rendered = engine._reserved_self_repair_request(
        "RUN-06578E9EDE icin kok neden ve "
        "sure olcumu sinirlarini incele."
    )

    assert rendered is not None
    assert "Durum: LOCAL_REVIEW" in rendered
    assert "Yerel inceleme:" in rendered


def test_plain_run_request_keeps_evidence_route() -> None:
    engine = _engine()

    rendered = engine._reserved_self_repair_request(
        "RUN-06578E9EDE bulgusunu goster"
    )

    assert rendered is not None
    assert "BULGU: RUN-06578E9EDE" in rendered
    assert "KANITA DAYALI ARASTIRMA PLANI" not in rendered


def test_fix_intent_keeps_implementation_priority() -> None:
    engine = _engine()
    engine.prepare_runtime_improvement_implementation = (
        lambda finding_id: f"FIX:{finding_id}"
    )

    rendered = engine._reserved_self_repair_request(
        "RUN-06578E9EDE bulgusunu arastir ve duzelt"
    )

    assert rendered == "FIX:RUN-06578E9EDE"


def test_maintenance_route_supports_run_research() -> None:
    engine = _engine()

    rendered = engine._maintenance_request(
        "RUN-06578E9EDE icin yerel cagri "
        "zincirini arastir"
    )

    assert rendered is not None
    assert "KANITA DAYALI ARASTIRMA PLANI" in rendered
    assert "Durum: LOCAL_REVIEW" in rendered
