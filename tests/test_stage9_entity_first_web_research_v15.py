from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


def test_factual_query_adds_exact_entity_lookup_before_relation_expansions() -> None:
    plan = ResearchManager.expanded_queries(
        "Marie Curie hangi alanda çalışmıştır",
        limit=5,
    )
    assert plan[0] == "Marie Curie hangi alanda çalışmıştır"
    assert plan[1].casefold() == '"marie curie"'
    lowered = [item.casefold() for item in plan]
    assert '"marie curie" field of work' in lowered


def test_unrelated_long_page_cannot_pass_via_incidental_entity_content() -> None:
    query = "Marie Curie hangi alanda çalışmıştır"
    source = ResearchSource(
        title="Area - What is Area?",
        url="https://example.com/math/area",
        snippet="Learn the definition and formulas for area.",
        content=(
            "A very long unrelated page. Footer biographies mention Marie Curie "
            "among thousands of unrelated names."
        ),
    )
    query_subjects = ResearchManager.subject_tokens(query)
    identity_tokens = ResearchManager.semantic_tokens(
        f"{source.title} {source.snippet} {source.url}"
    )
    assert query_subjects == {"marie", "curie"}
    assert not (query_subjects & identity_tokens)


def test_entity_result_metadata_carries_complete_identity_anchor() -> None:
    query = "Marie Curie hangi alanda çalışmıştır"
    source = ResearchSource(
        title="Marie Curie",
        url="https://example.org/people/marie-curie",
        snippet="Marie Curie was a physicist and chemist known for radioactivity research.",
    )
    subjects = ResearchManager.subject_tokens(query)
    identity = ResearchManager.semantic_tokens(
        f"{source.title} {source.snippet} {source.url}"
    )
    assert subjects <= identity
