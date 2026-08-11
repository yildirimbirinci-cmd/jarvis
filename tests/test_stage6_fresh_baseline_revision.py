from pathlib import Path


def _save_cycle_method() -> str:
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(
        encoding="utf-8"
    )
    start = source.index("    def _save_own_code_cycle(")
    end = source.index("\n    @staticmethod", start)
    return source[start:end]


def test_baseline_refreshes_revision_before_previous_revision_inheritance() -> None:
    method = _save_cycle_method()

    expected = (
        'if source_revision is None:\n'
        '                if str(stage) == "baseline":\n'
        '                    source_revision = AssistantEngine._current_own_code_revision()\n'
        '                else:\n'
        '                    source_revision = str(previous.get("source_revision", "") or "")'
    )
    assert expected in method


def test_non_baseline_states_still_inherit_existing_revision() -> None:
    method = _save_cycle_method()

    assert (
        'else:\n'
        '                    source_revision = str(previous.get("source_revision", "") or "")'
        in method
    )
    assert '"source_revision": str(source_revision or "")[:128]' in method
