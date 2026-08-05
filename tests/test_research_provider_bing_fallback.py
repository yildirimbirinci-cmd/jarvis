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
    ) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.encoding = "utf-8"
        self.status_code = status_code
        self.headers = {
            "content-type": "text/html",
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


def test_bing_is_used_after_ddg_challenges(
    monkeypatch,
) -> None:
    challenge = """
    <html>
      <body>
        Unfortunately, bots use DuckDuckGo too.
        Please complete the following challenge.
      </body>
    </html>
    """

    bing_result = """
    <html>
      <body>
        <ol>
          <li class="b_algo">
            <h2>
              <a href="https://docs.python.org/3/">
                Python Documentation
              </a>
            </h2>
            <div class="b_caption">
              <p>Official Python documentation.</p>
            </div>
          </li>
        </ol>
      </body>
    </html>
    """

    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)

        if "duckduckgo.com" in url:
            return FakeResponse(
                challenge,
                status_code=202,
            )

        if "bing.com/search" in url:
            return FakeResponse(bing_result)

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
    assert any(
        "bing.com/search" in url
        for url in calls
    )
