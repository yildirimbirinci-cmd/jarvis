from pathlib import Path


def _source() -> str:
    return (Path(__file__).resolve().parents[1] / 'core' / 'assistant.py').read_text(encoding='utf-8')


def test_production_repair_uses_strict_attempt_limit() -> None:
    source = _source()
    assert 'strict_attempt_limit: bool = False' in source
    assert 'attempts = base_attempts + 1' in source
    assert 'if attempt > base_attempts:' in source
    assert 'anchor_retry_guidance' in source
    assert 'helper_shape_retry_guidance' in source
    assert 'existing_helper_retry_guidance' in source
    assert 'strict_attempt_limit=production_repair' in source


def test_non_production_repair_keeps_dedicated_recovery_overflow() -> None:
    source = _source()
    assert 'attempts = base_attempts + 1' in source
