from pathlib import Path
from unittest.mock import Mock

import requests

from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


def test_fetch_uses_short_bounded_timeout(monkeypatch) -> None:
    response = Mock()
    response.status_code = 200
    response.headers = {"content-type": "text/html; charset=utf-8"}
    response.iter_content.return_value = [b"<html><body>Marie Curie</body></html>"]
    response.raise_for_status.return_value = None
    response.close.return_value = None

    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(
        ResearchManager,
        "_is_public_http_url",
        staticmethod(lambda url: True),
    )

    result = ResearchManager()._fetch(
        ResearchSource("Marie Curie", "https://example.org/marie", "snippet")
    )

    assert seen["timeout"] == 5
    assert "Marie Curie" in result.content


def test_fetch_timeout_fails_closed_to_search_metadata(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise requests.Timeout("slow page")

    monkeypatch.setattr(requests, "get", fail)
    monkeypatch.setattr(
        ResearchManager,
        "_is_public_http_url",
        staticmethod(lambda url: True),
    )

    source = ResearchSource("Entity", "https://example.org/entity", "useful snippet")
    result = ResearchManager()._fetch(source)

    assert result.title == source.title
    assert result.snippet == source.snippet
    assert result.content == ""


def test_bounded_stream_text_falls_back_when_response_encoding_is_not_string() -> None:
    response = Mock()
    response.iter_content.return_value = ["İstanbul".encode("utf-8")]
    response.encoding = Mock()

    text = ResearchManager._bounded_stream_text(response, 1024)

    assert text == "İstanbul"
