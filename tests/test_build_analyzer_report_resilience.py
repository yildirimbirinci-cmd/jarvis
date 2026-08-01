from artmach_assistant.core.build_analyzer import BuildAnalysis, BuildIssue

def test_report_ignores_non_issue_entries():
    report = BuildAnalysis([object(), BuildIssue("hata", "boom")]).report()
    assert "Toplam 1 sorun" in report

def test_report_handles_large_fields():
    report = BuildAnalysis([BuildIssue("hata", "x" * 10000, "f" * 5000, "1")]).report()
    assert len(report) < 10000
