from __future__ import annotations

from types import SimpleNamespace

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.code_review import CodeReviewIssue
from artmach_assistant.core.runtime_observability import RuntimeFinding


def _runtime_failure() -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-ABCDEF1234",
        severity="high",
        category="repeated_runtime_failure",
        title="Tekrarlanan uretim hatasi",
        explanation="Ayni hata tekrarlandi.",
        confidence=0.95,
        occurrence_count=4,
        last_seen="2026-08-04T10:00:00+00:00",
        workspace="C:/repo",
        scope="own_code",
        affected_paths=("core/example.py",),
        affected_symbols=("Example.run",),
        evidence=(),
        recommendation="Hedefli onarim hazirla.",
        acceptance_criteria=("Hata tekrar etmemeli.",),
        research_query="",
    )


def _engine(runtime_findings=()) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    runtime_report = SimpleNamespace(
        findings=tuple(runtime_findings),
    )
    engine._runtime_health_service = lambda: SimpleNamespace(
        analyze=lambda **_kwargs: runtime_report
    )
    engine._remembered_review = None
    engine._remember_action_context = lambda *args: setattr(
        engine,
        "_remembered_review",
        args,
    )
    return engine


def test_own_code_review_combines_runtime_and_static_evidence(
    monkeypatch,
) -> None:
    static_issue = CodeReviewIssue(
        kind="COMPLEXITY",
        path="core/example.py",
        line=10,
        message="run: 80 satirdan uzun fonksiyon",
        severity="medium",
    )
    analysis = SimpleNamespace(
        issues=(static_issue,),
    )

    class FakeReviewService:
        def __init__(self, _workspace) -> None:
            pass

        def analyze(self):
            return analysis

    monkeypatch.setattr(
        assistant_module,
        "CodeReviewService",
        FakeReviewService,
    )

    engine = _engine((_runtime_failure(),))

    result = engine.own_code_review_report()

    assert "A-gercek hata/guvenlik: 1" in result
    assert "B-kanitli teknik borc: 1" in result
    assert "C-statik inceleme ipucu: 0" in result
    assert "onarim adayi: 1" in result
    assert "Example.run" in result
    assert "salt okunurdur" in result
    assert engine._remembered_review is not None


def test_own_code_review_keeps_unmatched_complexity_as_static_hint(
    monkeypatch,
) -> None:
    static_issue = CodeReviewIssue(
        kind="COMPLEXITY",
        path="app.py",
        line=100,
        message="main: 80 satirdan uzun fonksiyon",
        severity="medium",
    )
    analysis = SimpleNamespace(
        issues=(static_issue,),
    )

    class FakeReviewService:
        def __init__(self, _workspace) -> None:
            pass

        def analyze(self):
            return analysis

    monkeypatch.setattr(
        assistant_module,
        "CodeReviewService",
        FakeReviewService,
    )

    engine = _engine()

    result = engine.own_code_review_report()

    assert "A-gercek hata/guvenlik: 0" in result
    assert "B-kanitli teknik borc: 0" in result
    assert "C-statik inceleme ipucu: 1" in result
    assert "Otomatik onarim adayi: hayir" in result


def test_own_code_review_survives_runtime_service_failure(
    monkeypatch,
) -> None:
    analysis = SimpleNamespace(issues=())

    class FakeReviewService:
        def __init__(self, _workspace) -> None:
            pass

        def analyze(self):
            return analysis

    monkeypatch.setattr(
        assistant_module,
        "CodeReviewService",
        FakeReviewService,
    )

    engine = AssistantEngine.__new__(AssistantEngine)
    engine._runtime_health_service = lambda: SimpleNamespace(
        analyze=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("runtime unavailable")
        )
    )
    engine._remember_action_context = lambda *_args: None

    result = engine.own_code_review_report()

    assert "Dogrulanabilir bir bakim bulgusu bulunamadi" in result
    assert "dosya degisikligi olusturulmadi" in result
