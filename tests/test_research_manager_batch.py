from __future__ import annotations

from types import SimpleNamespace

import pytest

from artmach_assistant.core.research_manager import (
    ResearchManager,
    ResearchResult,
    ResearchSource,
)


def test_search_many_deduplicates_queries_and_keeps_partial_success(monkeypatch) -> None:
    manager = ResearchManager()
    calls: list[str] = []

    def fake_search(query: str, max_results: int = 6) -> ResearchResult:
        calls.append(query)
        if query == "fails":
            raise RuntimeError("temporary")
        return ResearchResult(
            query,
            [ResearchSource(query, f"https://example.com/{query}", "snippet")],
        )

    monkeypatch.setattr(manager, "search", fake_search)
    results = manager.search_many(
        ["architecture", "Architecture", "fails", "testing"],
        max_results_per_query=2,
    )

    assert [result.query for result in results] == ["architecture", "testing"]
    assert calls == ["architecture", "fails", "testing"]


def test_search_many_raises_when_every_query_fails(monkeypatch) -> None:
    manager = ResearchManager()
    monkeypatch.setattr(
        manager,
        "search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    with pytest.raises(RuntimeError, match="tamamlanamadı"):
        manager.search_many(["one", "two"])


def test_private_and_credential_urls_are_rejected(monkeypatch) -> None:
    manager = ResearchManager()
    assert manager._is_public_http_url("http://127.0.0.1/admin") is False
    assert manager._is_public_http_url("http://user:pass@example.com/") is False
    assert manager._is_public_http_url("file:///etc/passwd") is False

    monkeypatch.setattr(
        "artmach_assistant.core.research_manager.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )
    assert manager._is_public_http_url("https://example.com/docs") is True


def test_fetch_does_not_request_private_destination(monkeypatch) -> None:
    manager = ResearchManager()
    called = False

    def fake_get(*_args, **_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace()

    monkeypatch.setattr(
        "artmach_assistant.core.research_manager.requests.get",
        fake_get,
    )
    source = ResearchSource("local", "http://127.0.0.1/secret", "")
    assert manager._fetch(source) == source
    assert called is False


def test_fetch_rejects_private_redirect_before_second_request(monkeypatch) -> None:
    manager = ResearchManager()
    calls: list[str] = []

    class _RedirectResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1/secret"}

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        manager,
        "_is_public_http_url",
        lambda url: not str(url).startswith("http://127.0.0.1"),
    )

    def fake_get(url: str, **_kwargs):
        calls.append(url)
        return _RedirectResponse()

    monkeypatch.setattr(
        "artmach_assistant.core.research_manager.requests.get",
        fake_get,
    )
    source = ResearchSource("public", "https://example.com/start", "")

    assert manager._fetch(source) == source
    assert calls == ["https://example.com/start"]
