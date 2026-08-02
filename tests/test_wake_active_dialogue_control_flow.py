import ast
from pathlib import Path


def test_sleep_mode_reaches_wake_listener_after_dialogue_extraction() -> None:
    source_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename="app.py")
    wake_worker = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WakeWordWorker"
    )
    methods = {
        node.name: node
        for node in wake_worker.body
        if isinstance(node, ast.FunctionDef)
    }
    helper = methods["_listen_active_dialogue"]
    run = methods["run"]

    assert any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and node.value.value is None
        for node in helper.body
    )
    assert any(
        isinstance(node, ast.If)
        and any(
            isinstance(item, ast.Constant) and item.value == "continue"
            for item in ast.walk(node.test)
        )
        and any(isinstance(item, ast.Continue) for item in node.body)
        for node in ast.walk(run)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "listen_for_local_wake"
        for node in ast.walk(run)
    )
