from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


def test_multitoken_subject_queries_get_quoted_relation_variants() -> None:
    plan = ResearchManager.expanded_queries(
        "Marie Curie hangi alanda çalışmıştır",
        limit=4,
    )
    assert plan[0] == "Marie Curie hangi alanda çalışmıştır"
    lowered = [row.casefold() for row in plan]
    assert '"marie curie" field of work' in lowered
    assert '"marie curie" scientific field' in lowered


def test_subject_gate_requires_all_tokens_for_short_named_entity() -> None:
    query_subjects = ResearchManager.subject_tokens(
        "Marie Curie hangi alanda çalışmıştır"
    )
    assert query_subjects == {"marie", "curie"}
    area_tokens = ResearchManager.semantic_tokens(
        "Area calculator Curie is also a unit used in physics"
    )
    assert query_subjects & area_tokens == {"curie"}
    assert len(query_subjects & area_tokens) != len(query_subjects)


def test_real_entity_page_contains_complete_subject_anchor() -> None:
    query_subjects = ResearchManager.subject_tokens(
        "Marie Curie hangi alanda çalışmıştır"
    )
    source_tokens = ResearchManager.semantic_tokens(
        "Marie Curie was a physicist and chemist who researched radioactivity"
    )
    assert query_subjects <= source_tokens
