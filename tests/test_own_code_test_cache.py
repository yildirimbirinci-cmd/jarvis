from __future__ import annotations

from artmach_assistant.core.own_code_test_cache import (
    CACHE_TTL_SECONDS,
    load_baseline_cache,
    save_baseline_cache,
)


def _source(root, content: str = "x = 1\n") -> None:
    (root / "core").mkdir(exist_ok=True)
    (root / "core" / "sample.py").write_text(content, encoding="utf-8")


def test_exact_recent_source_reuses_baseline(tmp_path) -> None:
    cache = tmp_path / "cache.json"
    source = tmp_path / "project"
    source.mkdir()
    _source(source)
    save_baseline_cache(cache, source, False, "FAILED old", now=1000)

    result = load_baseline_cache(cache, source, now=1001)

    assert result is not None
    assert not result.success
    assert result.output == "FAILED old"


def test_source_change_invalidates_baseline(tmp_path) -> None:
    cache = tmp_path / "cache.json"
    source = tmp_path / "project"
    source.mkdir()
    _source(source)
    save_baseline_cache(cache, source, True, "passed", now=1000)
    _source(source, "x = 2\n")
    assert load_baseline_cache(cache, source, now=1001) is None


def test_expired_or_future_cache_is_rejected(tmp_path) -> None:
    cache = tmp_path / "cache.json"
    source = tmp_path / "project"
    source.mkdir()
    _source(source)
    save_baseline_cache(cache, source, True, "passed", now=1000)
    assert load_baseline_cache(
        cache, source, now=1000 + CACHE_TTL_SECONDS + 1
    ) is None
    assert load_baseline_cache(cache, source, now=999) is None


def test_ignored_runtime_files_do_not_invalidate_source(tmp_path) -> None:
    cache = tmp_path / "cache.json"
    source = tmp_path / "project"
    source.mkdir()
    _source(source)
    save_baseline_cache(cache, source, True, "passed", now=1000)
    runtime = source / "__pycache__"
    runtime.mkdir()
    (runtime / "sample.pyc").write_bytes(b"runtime")
    assert load_baseline_cache(cache, source, now=1001) is not None
