from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


def test_single_token_identity_question_gets_exact_entity_seed() -> None:
    assert ResearchManager.entity_seed_query("Atatürk kimdir?") == '"ataturk"'
    assert ResearchManager.entity_seed_query("Einstein kimdir?") == '"einstein"'
    assert ResearchManager.entity_seed_query("Python nedir?") == '"python"'


def test_identity_answer_relevance_uses_subject_not_question_word() -> None:
    answer = (
        "Mustafa Kemal Atatürk, Türkiye Cumhuriyeti'nin kurucusu ve "
        "ilk cumhurbaşkanıdır."
    )
    assert ResearchManager.answer_relevant_to_query("Atatürk kimdir?", answer)


def test_identity_evidence_accepts_subject_anchored_biography_sentence() -> None:
    sources = [
        ResearchSource(
            title="Mustafa Kemal Atatürk",
            url="https://example.org/ataturk",
            snippet=(
                "Mustafa Kemal Atatürk was the founder and first president "
                "of the Republic of Turkey."
            ),
            content="",
        )
    ]
    evidence = ResearchManager.query_evidence("Atatürk kimdir?", sources)
    assert evidence
    assert "Atatürk" in evidence[0][0]


def test_identity_evidence_confidence_treats_subject_biography_as_direct() -> None:
    sources = [
        ResearchSource(
            title="Mustafa Kemal Atatürk",
            url="https://en.wikipedia.org/wiki/Mustafa_Kemal_Atat%C3%BCrk",
            snippet=(
                "Mustafa Kemal Atatürk was the founder and first president "
                "of the Republic of Turkey."
            ),
            content="",
        )
    ]
    answer = "Mustafa Kemal Atatürk Türkiye Cumhuriyeti'nin kurucusudur."
    assessment = ResearchManager.assess_evidence(
        "Atatürk kimdir?",
        answer,
        sources,
    )
    assert assessment.passages
    assert assessment.confidence >= 0.70
