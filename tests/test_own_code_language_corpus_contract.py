from __future__ import annotations

from artmach_assistant.core.own_code_language_intelligence import load_language_corpus


def test_language_corpus_has_broad_coverage() -> None:
    corpus = load_language_corpus()
    intents = corpus["intents"]
    assert len(intents["CREATE_PROPOSAL"]["positive"]) >= 30
    assert len(intents["APPLY_PENDING"]["positive"]) >= 15
    assert len(intents["CREATE_PLAN"]["positive"]) >= 10
    assert len(intents["REPORT_ENGINEERING_STATE"]["positive"]) >= 10
    assert len(intents["REPORT_GIT_STATE"]["positive"]) >= 10


def test_language_corpus_keeps_apply_and_do_not_apply_separate() -> None:
    corpus = load_language_corpus()
    jargon = corpus["jargon"]
    assert "uygula" in jargon["apply"]
    assert "uygulama" in jargon["do_not_apply"]
    assert "apply" in jargon["apply"]
    assert "do not apply" in jargon["do_not_apply"]
