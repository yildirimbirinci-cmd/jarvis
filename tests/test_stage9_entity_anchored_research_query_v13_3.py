from artmach_assistant.core.research_manager import ResearchManager


def test_unknown_field_relation_gets_deterministic_subject_anchored_variants() -> None:
    plan = ResearchManager.compose_query_plan(
        "Marie Curie hangi alanda çalışmıştır",
        ("area", "scientific field"),
    )
    assert plan[0] == "Marie Curie hangi alanda çalışmıştır"
    lowered = [item.casefold() for item in plan]
    assert any("marie" in item and "curie" in item and "scientific field" in item for item in lowered)
    assert all(
        {"marie", "curie"} & ResearchManager.semantic_tokens(item)
        for item in plan
    )
    assert "area" not in lowered


def test_unrelated_planner_drift_cannot_become_unanchored_search() -> None:
    plan = ResearchManager.compose_query_plan(
        "Ada Lovelace hangi alanda çalışmıştır",
        ("mathematics", "computer programming"),
    )
    assert all(
        {"ada", "lovelace"} & ResearchManager.semantic_tokens(item)
        for item in plan
    )


def test_known_relation_expansions_keep_subject_anchor() -> None:
    plan = ResearchManager.compose_query_plan(
        "Satürn'ün en büyük uydusu hangi ülkede keşfedildi",
        (),
    )
    subjects = ResearchManager.subject_tokens(
        "Satürn'ün en büyük uydusu hangi ülkede keşfedildi"
    )
    assert subjects
    assert all(
        subjects & ResearchManager.semantic_tokens(item)
        for item in plan
    )


def test_unanchored_unrelated_planner_drift_is_rejected() -> None:
    plan = ResearchManager.compose_query_plan(
        "Ada Lovelace hangi meslekle tanınır",
        (
            "Ada Lovelace occupation profession",
            "Ada Lovelace known for",
            "unrelated football results",
        ),
    )
    assert "Ada Lovelace occupation profession" in plan
    assert "Ada Lovelace known for" in plan
    assert all("football" not in row.casefold() for row in plan)
