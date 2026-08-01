from pathlib import Path

from pkg19work.indexing.call_graph.model import CallGraphDiagnosticsReport, CallGraphFileDiagnostics
from pkg19work.indexing.call_graph.parser import CallSiteParser


def test_parser_converts_ast_recursion_failure_to_parse_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "deep.py"
    source.write_text("target()\n", encoding="utf-8")

    def fail_parse(*args, **kwargs):
        raise RecursionError("AST nesting is too deep")

    monkeypatch.setattr("pkg19work.indexing.call_graph.parser.ast.parse", fail_parse)
    calls, error = CallSiteParser().parse_file(source)

    assert calls == ()
    assert error == "RecursionError: AST nesting is too deep"


def test_parser_converts_visitor_recursion_failure_to_parse_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "visitor.py"
    source.write_text("target()\n", encoding="utf-8")

    def fail_visit(self, tree):
        raise RecursionError("visitor depth exceeded")

    monkeypatch.setattr("pkg19work.indexing.call_graph.parser._CallVisitor.visit", fail_visit)
    calls, error = CallSiteParser().parse_file(source)

    assert calls == ()
    assert error == "RecursionError: visitor depth exceeded"


def test_diagnostic_resolution_rates_are_finite_and_bounded() -> None:
    file_report = CallGraphFileDiagnostics("x.py", 0, -5, 2, 0)
    project_report = CallGraphDiagnosticsReport((), 0, float("nan"), 1, 0, 0)

    assert file_report.resolution_rate == 0.0
    assert project_report.resolution_rate == 1.0
    assert CallGraphFileDiagnostics("x.py", 0, 5, -2, 0).resolution_rate == 1.0
