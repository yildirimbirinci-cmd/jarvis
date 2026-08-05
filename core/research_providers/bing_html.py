from __future__ import annotations

import base64
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from artmach_assistant.core.research_providers.base import (
    ProviderSearchResult,
    SearchProvider,
)


class BingHtmlProvider(SearchProvider):
    name = "bing_html"
    endpoint = "https://www.bing.com/search"

    @staticmethod
    def _target_url(url: str) -> str:
        try:
            parsed = urlparse(url)
        except ValueError:
            return url

        host = str(parsed.hostname or "").casefold()

        if not host.endswith("bing.com"):
            return url

        encoded = parse_qs(parsed.query).get("u", [""])[0]

        if not encoded:
            return url

        encoded = unquote(encoded)

        if encoded.startswith("a1"):
            encoded = encoded[2:]

        padding = "=" * (-len(encoded) % 4)

        try:
            decoded = base64.urlsafe_b64decode(
                encoded + padding
            ).decode("utf-8", errors="strict")
        except (ValueError, UnicodeError):
            return url

        if decoded.startswith(("http://", "https://")):
            return decoded

        return url

    def search(
        self,
        query: str,
        max_results: int,
    ) -> list[ProviderSearchResult]:
        response = self.http_get(
            self.endpoint,
            params={
                "q": query,
                "count": max_results,
                "setlang": "en",
            },
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=20,
        )
        response.raise_for_status()
        raw_html = self.bounded_response_text(
            response,
            self.max_html_bytes,
        )
        soup = BeautifulSoup(raw_html, "html.parser")
        rows: list[ProviderSearchResult] = []
        seen: set[str] = set()

        for result in soup.select("li.b_algo"):
            link = result.select_one("h2 a[href]")

            if link is None:
                continue

            url = self._target_url(
                self.clean_url(
                    str(link.get("href", "") or "")
                )
            )
            canonical = self.canonical_url(url)

            if (
                not canonical
                or canonical in seen
                or not self.public_url_validator(url)
            ):
                continue

            title = link.get_text(" ", strip=True)

            if not title:
                continue

            snippet_node = result.select_one(
                ".b_caption p"
            )
            if snippet_node is None:
                snippet_node = result.select_one("p")

            snippet = (
                snippet_node.get_text(" ", strip=True)
                if snippet_node is not None
                else ""
            )

            seen.add(canonical)
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
