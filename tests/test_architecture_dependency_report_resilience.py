from artmach_assistant.core.architecture_service import DependencyGraph

class Broken:
    def __str__(self): raise RuntimeError("boom")

def test_broken_focus_and_boolean_limit_do_not_crash():
    graph = DependencyGraph()
    graph.add("a.py", "b.py")
    assert "BAĞIMLILIK" in graph.report(Broken(), True)
