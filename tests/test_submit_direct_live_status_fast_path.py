from __future__ import annotations

from pathlib import Path


def test_submit_handles_live_status_before_submit_text() -> None:
    source = Path(__file__).resolve().parents[1] / "app.py"
    text = source.read_text(encoding="utf-8")
    submit_start = text.index("    def submit(self) -> None:")
    submit_end = text.index("\n    def submit_text", submit_start)
    block = text[submit_start:submit_end]

    assert "submit_direct_fast_path" in block
    assert "build_live_status_answer(self.engine, None)" in block
    assert block.index("if worker_running and (live_status or live_cancel):") < block.index(
        "self.submit_text(text)"
    )
    assert "self.busy()" not in block
    assert "task_orchestrator.active" not in block


def test_submit_direct_fast_path_writes_all_trace_stages() -> None:
    source = Path(__file__).resolve().parents[1] / "app.py"
    text = source.read_text(encoding="utf-8")
    submit_start = text.index("    def submit(self) -> None:")
    submit_end = text.index("\n    def submit_text", submit_start)
    block = text[submit_start:submit_end]

    for event in (
        "TEXT_SUBMITTED",
        "LIVE_QUERY_CLASSIFIED",
        "STATUS_READ",
        "RESPONSE_RENDERED",
    ):
        assert f'"{event}"' in block
