from __future__ import annotations

from artmach_assistant.core.research_manager import (
    ResearchManager,
)


class FakeResponse:
    def __init__(
        self,
        text: str,
        *,
        status_code: int = 200,
        content_type: str = "text/html",
    ) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.encoding = "utf-8"
        self.status_code = status_code
        self.headers = {
            "content-type": content_type,
        }
        self.url = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(
                f"HTTP {self.status_code}"
            )

    def close(self) -> None:
        return None

    def iter_content(
        self,
        chunk_size: int = 65536,
    ):
        yield self.content


def test_search_falls_back_to_lite(monkeypatch) -> None:
    html_empty = "<html><body></body></html>"
    lite_result = """
    <html>
      <body>
        <a class="result-link"
           href="https://docs.python.org/3/">
          Python Documentation
        </a>
        <div>Official Python documentation.</div>
      </body>
    </html>
    """

    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)

        if "html.duckduckgo.com" in url:
            return FakeResponse(html_empty)

        if "lite.duckduckgo.com" in url:
            return FakeResponse(lite_result)

        return FakeResponse(
            "<html><body>Python docs</body></html>"
        )

    monkeypatch.setattr(
        "artmach_assistant.core.research_manager.requests.get",
        fake_get,
    )
    monkeypatch.setattr(
        ResearchManager,
        "_is_public_http_url",
        staticmethod(lambda url: True),
    )

    result = ResearchManager().search(
        "Python official documentation",
        max_results=3,
    )

    assert len(result.sources) == 1
    assert (
        result.sources[0].title
        == "Python Documentation"
    )
    assert any(
        "html.duckduckgo.com" in url
        for url in calls
    )
    assert any(
        "lite.duckduckgo.com" in url
        for url in calls
    )


def test_search_uses_html_without_lite(
    monkeypatch,
) -> None:
    html_result = """
    <html>
      <body>
        <div class="result">
          <a class="result__a"
             href="https://docs.python.org/3/">
            Python Documentation
          </a>
          <div class="result__snippet">
            Official documentation.
          </div>
        </div>
      </body>
    </html>
    """

    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(html_result)

    monkeypatch.setattr(
        "artmach_assistant.core.research_manager.requests.get",
        fake_get,
    )
    monkeypatch.setattr(
        ResearchManager,
        "_is_public_http_url",
        staticmethod(lambda url: True),
    )

    result = ResearchManager().search(
        "Python official documentation",
        max_results=3,
    )

    assert len(result.sources) == 1
    assert not any(
        "lite.duckduckgo.com" in url
        for url in calls
    )
