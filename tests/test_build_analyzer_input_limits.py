from artmach_assistant.core.build_analyzer import BuildLogAnalyzer

def test_analyzer_caps_issue_count():
    output = "\n".join(f"ERROR: boom {i}" for i in range(6000))
    result = BuildLogAnalyzer().analyze(output)
    assert len(result.issues) == 5000

def test_analyzer_removes_nul_and_caps_message():
    result = BuildLogAnalyzer().analyze("ERROR: " + "x\x00" * 5000)
    assert "\x00" not in result.issues[0].message
    assert len(result.issues[0].message) <= 4000
