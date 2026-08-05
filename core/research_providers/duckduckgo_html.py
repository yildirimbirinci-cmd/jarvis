from __future__ import annotations

from bs4 import BeautifulSoup

from artmach_assistant.core.research_providers.base import (
    ProviderSearchResult,
    SearchProvider,
)


class DuckDuckGoHtmlProvider(SearchProvider):
    name = "duckduckgo_html"
    endpoint = "https://html.duckduckgo.com/html/"

    def search(
        self,
        query: str,
        max_results: int,
    ) -> list[ProviderSearchResult]:
        response = self.http_get(
            self.endpoint,
            params={"q": query},
            headers={"User-Agent": self.user_agent},
            timeout=20,
        )
        response.raise_for_status()
        raw_html = self.bounded_response_text(
            response,
            self.max_html_bytes,
        )
        challenge_text = raw_html.casefold()

        if (
            int(getattr(response, "status_code", 0) or 0) == 202
            or "please complete the following challenge" in challenge_text
            or "bots use duckduckgo too" in challenge_text
        ):
            raise RuntimeError(
                "DuckDuckGo challenge page returned."
            )

        soup = BeautifulSoup(raw_html, "html.parser")
        rows: list[ProviderSearchResult] = []
        seen: set[str] = set()

        for result in soup.select(".result"):
            link = result.select_one(".result__a")
            if link is None:
                continue

            url = self.clean_url(
                str(link.get("href", "") or "")
            )
            canonical = self.canonical_url(url)

            if (
                not canonical
                or canonical in seen
                or not self.public_url_validator(url)
            ):
                continue

            seen.add(canonical)
            snippet_node = result.select_one(
                ".result__snippet"
            )
            snippet = (
                snippet_node.get_text(" ", strip=True)
                if snippet_node is not None
                else ""
            )
            title = link.get_text(" ", strip=True)

            if not title:
                continue

            rows.append(
                ProviderSearchResult(
                    title=title[:500],
                    url=url[:4000],
                    snippet=snippet[:2000],
                )
            )

            if len(rows) >= max_results:
                break

        return rows
