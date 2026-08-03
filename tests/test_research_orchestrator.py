from __future__ import annotations

from artmach_assistant.core.research_orchestrator import ResearchOrchestrator


def test_missing_evidence_prefers_runtime_logs_before_other_work() -> None:
    strategy = ResearchOrchestrator().plan({
        "request": "Ses sorunlarını gider",
        "domain": "voice",
        "status": "needs_evidence",
        "findings": [],
    })
    assert strategy.status == "ready"
    assert strategy.actions[0].kind == "runtime_logs"
    assert strategy.actions[0].required is True


def test_external_domain_requires_explicit_web_permission() -> None:
    strategy = ResearchOrchestrator().plan({
        "request": "3ds Max API özelliklerini araştır",
        "domain": "3ds_max",
        "status": "investigating",
    })
    web = next(item for item in strategy.actions if item.kind == "web_research")
    assert strategy.status == "waiting_permission"
    assert web.permission_required is True


def test_explicit_permission_makes_web_research_ready() -> None:
    strategy = ResearchOrchestrator().plan({
        "request": "Telefon bağlantısı için API araştır",
        "domain": "mobile",
        "status": "investigating",
    }, allow_web_research=True)
    web = next(item for item in strategy.actions if item.kind == "web_research")
    assert strategy.status == "ready"
    assert web.permission_required is False


def test_history_is_consulted_when_repository_paths_are_available() -> None:
    strategy = ResearchOrchestrator().plan({
        "request": "Hafıza neden unutuyor",
        "domain": "knowledge_memory",
        "status": "investigating",
    }, knowledge_paths=["knowledge/repository.json"])
    assert any(item.kind == "knowledge_history" for item in strategy.actions)
