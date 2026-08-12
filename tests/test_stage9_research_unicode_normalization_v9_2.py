from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


def test_semantic_tokens_preserve_turkish_dotted_capital_i_subject() -> None:
    tokens = ResearchManager.semantic_tokens("Hayır. İstanbul Türkiye'nin başkenti değildir.")
    assert "istanbul" in tokens
    assert "stanbul" not in tokens


def test_deictic_capital_evidence_is_relevant_after_unicode_normalization() -> None:
    query = "istanbul bir başkentmidir"
    answer = "Hayır. İstanbul Türkiye'nin başkenti değildir; Türkiye'nin başkenti Ankara'dır."
    sources = [
        ResearchSource(
            title="Turkey (Türkiye)",
            url="https://example.com/turkiye",
            snippet="Ankara is the capital city of Turkey.",
            content="Istanbul is not the capital of Turkey. Ankara is the capital city, while Istanbul is the largest city.",
        )
    ]
    assert ResearchManager.answer_relevant_to_query(query, answer) is True
    supporting = ResearchManager.supporting_evidence(query, answer, sources)
    assert supporting
    assert "Istanbul is not the capital of Turkey" in supporting[0][0]
