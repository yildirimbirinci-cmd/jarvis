from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


def test_expanded_query_preserves_original_entity_phrase_order() -> None:
    plan = ResearchManager.expanded_queries(
        "Marie Curie hangi alanda çalışmıştır",
        limit=4,
    )
    assert plan[0] == "Marie Curie hangi alanda çalışmıştır"
    lowered = [item.casefold().replace('"', "") for item in plan]
    assert "marie curie field of work" in lowered
    assert "marie curie scientific field" in lowered
    assert all("curie marie" not in row.casefold() for row in plan)


def test_area_only_source_does_not_satisfy_marie_curie_subject_anchor() -> None:
    query = "Marie Curie hangi alanda çalışmıştır"
    source = ResearchSource(
        title="Area - Wikipedia",
        url="https://en.wikipedia.org/wiki/Area",
        snippet="Area is the measure of a region in a plane.",
        content="Area is a mathematical quantity.",
    )
    subjects = ResearchManager.subject_tokens(query)
    source_tokens = ResearchManager.semantic_tokens(
        f"{source.title} {source.snippet} {source.content}"
    )
    assert subjects
    assert not (subjects & source_tokens)


def test_entity_source_satisfies_subject_anchor() -> None:
    query = "Marie Curie hangi alanda çalışmıştır"
    source = ResearchSource(
        title="Marie Curie",
        url="https://example.org/marie-curie",
        snippet="Marie Curie was a physicist and chemist.",
        content="Marie Curie conducted pioneering research on radioactivity.",
    )
    subjects = ResearchManager.subject_tokens(query)
    source_tokens = ResearchManager.semantic_tokens(
        f"{source.title} {source.snippet} {source.content}"
    )
    assert subjects & source_tokens
