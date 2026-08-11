from pathlib import Path


def _save_cycle_source() -> str:
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(
        encoding="utf-8"
    )
    start = source.index("    def _save_own_code_cycle(")
    end = source.index("\n    @staticmethod", start)
    return source[start:end]


def test_legacy_recovery_transition_does_not_invent_source_revision() -> None:
    method = _save_cycle_source()

    assert (
        'if not source_revision and str(stage) == "baseline":'
        in method
    )
    assert (
        'if not source_revision:\n'
        '                source_revision = AssistantEngine._current_own_code_revision()'
        not in method
    )


def test_new_baseline_captures_source_revision() -> None:
    method = _save_cycle_source()

    expected = (
        'if not source_revision and str(stage) == "baseline":\n'
        '                source_revision = AssistantEngine._current_own_code_revision()'
    )
    assert expected in method


def test_existing_revision_is_preserved_before_baseline_capture() -> None:
    method = _save_cycle_source()

    preserve = (
        'if source_revision is None:\n'
        '                source_revision = str(previous.get("source_revision", "") or "")'
    )
    capture = 'if not source_revision and str(stage) == "baseline":'

    preserve_pos = method.index(preserve)
    capture_pos = method.index(capture)

    assert preserve_pos < capture_pos
    assert '"source_revision": str(source_revision or "")[:128]' in method
