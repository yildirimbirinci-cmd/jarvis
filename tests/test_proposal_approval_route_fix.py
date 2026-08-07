from pathlib import Path


def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")


def test_pending_proposal_approval_precedes_reserved_self_repair():
    source = _source()
    start = source.index("def handle_local_command")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    approval = block.index("own_code_approval = self._own_code_approval_request(text)")
    repair = block.index(
        "reserved_self_repair = self._reserved_self_repair_request(text)"
    )
    assert approval < repair


def test_early_approval_route_is_narrow():
    source = _source()
    start = source.index("def handle_local_command")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    assert "pending_own_code is not None" in block
    assert "supplied_proposal_id or explicit_proposal_approval" in block
    assert '"taslagi onayla"' in block
    assert '"kod degisikligini uygula"' in block


def test_existing_reserved_self_repair_route_is_preserved():
    source = _source()
    start = source.index("def handle_local_command")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    assert (
        "reserved_self_repair = self._reserved_self_repair_request(text)"
        in block
    )
    assert "if reserved_self_repair is not None:" in block
