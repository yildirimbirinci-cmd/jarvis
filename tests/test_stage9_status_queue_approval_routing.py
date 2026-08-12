from __future__ import annotations

import inspect
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def test_pending_approval_status_report_is_read_only_contract():
    source = inspect.getsource(AssistantEngine.pending_approval_status_report)
    assert "pending_evidence_research.json" in source
    assert "_own_code_pending_proposal_store" in source
    assert "hicbir onay, iptal, plan veya gorev baslatilmadi" in source


def test_app_routes_stage9_status_queue_approval_before_engine_worker():
    import ast
    from pathlib import Path

    text = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    main = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    submit = next(
        node for node in main.body
        if isinstance(node, ast.FunctionDef) and node.name == "submit_text"
    )
    source = ast.get_source_segment(text, submit)
    assert source is not None
    fast = source.index("self._stage9_read_only_backend_query(text)")
    worker = source.index("lambda: self.engine.handle(text)")
    assert fast < worker
    assert 'route="stage9_read_only_backend"' in source


def test_stage9_read_only_backend_helper_covers_live_acceptance_phrases():
    import ast
    from pathlib import Path

    text = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    main = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    helper = next(
        node for node in main.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_stage9_read_only_backend_query"
    )
    source = ast.get_source_segment(text, helper)
    assert source is not None
    assert "calisan gorevlerin durumunu goster" in source
    assert "kuyrukta bekleyen gorev" in source
    assert "bekleyen onay" in source
    assert "self.task_orchestrator.pending" in source
    assert "pending_approval_status_report" in source
