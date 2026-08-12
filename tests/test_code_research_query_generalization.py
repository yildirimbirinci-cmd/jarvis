from artmach_assistant.core.code_research_query_planner import plan_external_code_queries


def test_query_planner_is_target_aware_without_known_issue_branches():
    queries = plan_external_code_queries(
        title="Unexpected adapter contract failure",
        path="plugins/adapter.py",
        symbol="Adapter.execute",
    )
    rendered = "\n".join(queries).casefold()
    assert "adapter.execute" in rendered or "execute" in rendered
    assert "unexpected adapter contract failure" in rendered
    assert "official documentation" in rendered
    assert "github issue" in rendered


def test_query_planner_does_not_inject_performance_for_unrelated_problem():
    queries = plan_external_code_queries(
        title="Import contract mismatch",
        path="core/importer.py",
        symbol="Importer.load",
    )
    assert all("performance" not in query.casefold() for query in queries)


def test_query_planner_preserves_problem_context_instead_of_named_finding_rules():
    a = plan_external_code_queries(
        title="Cancellation state leak",
        path="core/task.py",
        symbol="Task.cancel",
    )
    b = plan_external_code_queries(
        title="Parser unicode regression",
        path="core/parser.py",
        symbol="Parser.parse",
    )
    assert a != b
    assert any("cancellation state leak" in q.casefold() for q in a)
    assert any("parser unicode regression" in q.casefold() for q in b)
