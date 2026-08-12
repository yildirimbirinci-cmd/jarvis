from __future__ import annotations

from bs4 import BeautifulSoup

from artmach_assistant.core.research_providers.base import (
    ProviderSearchResult,
    SearchProvider,
)


class DuckDuckGoLiteProvider(SearchProvider):
    name = "duckduckgo_lite"
    endpoint = "https://lite.duckduckgo.com/lite/"

    def search(
        self,
        query: str,
        max_results: int,
    ) -> list[ProviderSearchResult]:
        response = self.http_get(
            self.endpoint,
            params={"q": query},
            headers={"User-Agent": self.user_agent},
            timeout=6,
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

        links = soup.select("a.result-link")
        if not links:
            links = soup.select(
                "a[href][class*=result]"
            )

        for link in links:
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
            title = link.get_text(" ", strip=True)

            if not title:
                continue

            snippet = ""
            parent = link.parent

            if parent is not None:
                sibling = parent.find_next_sibling()
                if sibling is not None:
                    snippet = sibling.get_text(
                        " ",
                        strip=True,
                    )

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
