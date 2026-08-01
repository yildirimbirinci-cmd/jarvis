from __future__ import annotations

import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


_MAX_SEARCH_RESULTS = 20
_MAX_QUERY_COUNT = 8
_MAX_SEARCH_HTML_BYTES = 2_000_000
_MAX_PAGE_BYTES = 2_000_000
_MAX_PAGE_TEXT_CHARS = 16_000
_MAX_REDIRECTS = 5


@dataclass(frozen=True)
class ResearchSource:
    title: str
    url: str
    snippet: str
    content: str = ""


@dataclass
class ResearchResult:
    query: str
    sources: list[ResearchSource]
    summary: str = ""

    def source_text(self) -> str:
        rows = []
        for index, source in enumerate(self.sources, 1):
            rows.append(
                f"[{index}] {source.title}\nURL: {source.url}\n"
                f"Özet: {source.snippet}\nİçerik:\n{source.content[:7000]}"
            )
        return "\n\n".join(rows)

    def report(self) -> str:
        source_list = "\n".join(
            f"[{index}] {source.title}\n    {source.url}"
            for index, source in enumerate(self.sources, 1)
        )
        return f"ARAŞTIRMA: {self.query}\n\n{self.summary}\n\nKAYNAKLAR\n{source_list}"


class ResearchManager:
    """Explicit, bounded web research for user-approved questions.

    Search is intentionally separate from code editing.  Returned pages are
    treated as untrusted evidence: local/private network destinations are
    rejected, downloads are size bounded, and only text/html is hydrated.
    """

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ArtmachAssistant/0.5"

    def search(self, query: str, max_results: int = 6) -> ResearchResult:
        query = self._validated_query(query)
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise TypeError("max_results pozitif bir tam sayı olmalıdır.")
        if max_results <= 0:
            raise ValueError("max_results sıfırdan büyük olmalıdır.")
        max_results = min(max_results, _MAX_SEARCH_RESULTS)

        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": self.USER_AGENT},
            timeout=20,
        )
        response.raise_for_status()
        raw_html = self._bounded_response_text(response, _MAX_SEARCH_HTML_BYTES)
        soup = BeautifulSoup(raw_html, "html.parser")
        sources: list[ResearchSource] = []
        seen: set[str] = set()
        for result in soup.select(".result"):
            link = result.select_one(".result__a")
            if not link:
                continue
            url = self._clean_url(link.get("href", ""))
            canonical = self._canonical_url(url)
            if not canonical or canonical in seen or not self._is_public_http_url(url):
                continue
            seen.add(canonical)
            snippet_node = result.select_one(".result__snippet")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            sources.append(
                ResearchSource(
                    link.get_text(" ", strip=True)[:500],
                    url[:4000],
                    snippet[:2000],
                )
            )
            if len(sources) >= max_results:
                break
        if not sources:
            raise RuntimeError("Arama motorundan güvenli bir sonuç alınamadı. İnternet bağlantısını kontrol et.")
        hydrated = [self._fetch(source) for source in sources]
        return ResearchResult(query=query, sources=hydrated)

    def search_many(
        self,
        queries: object,
        *,
        max_results_per_query: int = 4,
    ) -> list[ResearchResult]:
        """Run a bounded set of distinct research questions.

        A failure for one query does not erase successful evidence from other
        queries.  When every query fails, the first useful error is surfaced.
        """

        if isinstance(max_results_per_query, bool) or not isinstance(max_results_per_query, int):
            raise TypeError("Sorgu başına sonuç limiti pozitif tam sayı olmalıdır.")
        if max_results_per_query <= 0:
            raise ValueError("Sorgu başına sonuç limiti sıfırdan büyük olmalıdır.")
        if isinstance(queries, (str, bytes)):
            raw_queries = (queries,)
        else:
            try:
                raw_queries = tuple(queries)  # type: ignore[arg-type]
            except TypeError as exc:
                raise TypeError("Araştırma sorguları yinelenebilir olmalıdır.") from exc

        unique: list[str] = []
        seen: set[str] = set()
        for raw in raw_queries:
            query = self._validated_query(raw)
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(query)
            if len(unique) >= _MAX_QUERY_COUNT:
                break
        if not unique:
            raise ValueError("En az bir araştırma sorgusu gereklidir.")

        results: list[ResearchResult] = []
        errors: list[str] = []
        for query in unique:
            try:
                results.append(
                    self.search(query, max_results=min(max_results_per_query, _MAX_SEARCH_RESULTS))
                )
            except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
                errors.append(f"{query}: {exc}")
        if not results:
            detail = "; ".join(errors[:3]) or "sonuç alınamadı"
            raise RuntimeError("Araştırma sorguları tamamlanamadı: " + detail)
        return results

    def _fetch(self, source: ResearchSource) -> ResearchSource:
        if not self._is_public_http_url(source.url):
            return source
        current_url = source.url
        try:
            for _redirect_count in range(_MAX_REDIRECTS + 1):
                if not self._is_public_http_url(current_url):
                    return source
                response = requests.get(
                    current_url,
                    headers={"User-Agent": self.USER_AGENT},
                    timeout=15,
                    allow_redirects=False,
                    stream=True,
                )
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code in {301, 302, 303, 307, 308}:
                    location = str(
                        getattr(response, "headers", {}).get("location", "")
                    ).strip()
                    response.close()
                    if not location:
                        return source
                    next_url = urljoin(current_url, location)
                    # Validate before the redirect request is sent. Checking only
                    # response.url after automatic redirects would already have
                    # contacted a private/local destination.
                    if not self._is_public_http_url(next_url):
                        return source
                    current_url = next_url
                    continue

                response.raise_for_status()
                content_type = str(
                    getattr(response, "headers", {}).get("content-type", "")
                ).casefold()
                if "text/html" not in content_type:
                    response.close()
                    return ResearchSource(
                        source.title, current_url[:4000], source.snippet, ""
                    )
                page = self._bounded_stream_text(response, _MAX_PAGE_BYTES)
                soup = BeautifulSoup(page, "html.parser")
                for tag in soup([
                    "script", "style", "nav", "footer", "header",
                    "noscript", "svg",
                ]):
                    tag.decompose()
                page_text = soup.get_text(" ", strip=True)
                page_text = re.sub(r"\s+", " ", html.unescape(page_text))
                return ResearchSource(
                    source.title,
                    current_url[:4000],
                    source.snippet,
                    page_text[:_MAX_PAGE_TEXT_CHARS],
                )
            return source
        except (requests.RequestException, OSError, UnicodeError, ValueError):
            return source

    @staticmethod
    def _validated_query(query: object) -> str:
        if not isinstance(query, str):
            raise TypeError("Araştırma sorgusu metin olmalıdır.")
        cleaned = re.sub(r"\s+", " ", query).strip()
        if not cleaned:
            raise ValueError("Araştırma sorgusu boş olamaz.")
        if len(cleaned) > 1000:
            raise ValueError("Araştırma sorgusu 1000 karakteri aşamaz.")
        return cleaned

    @staticmethod
    def _bounded_response_text(response: object, max_bytes: int) -> str:
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            raw = content[: max_bytes + 1]
            if len(raw) > max_bytes:
                raise RuntimeError("Arama yanıtı güvenli boyut sınırını aşıyor.")
            encoding = getattr(response, "encoding", None) or "utf-8"
            return raw.decode(encoding, errors="replace")
        text = str(getattr(response, "text", ""))
        raw = text.encode("utf-8", errors="replace")
        if len(raw) > max_bytes:
            raise RuntimeError("Arama yanıtı güvenli boyut sınırını aşıyor.")
        return text

    @staticmethod
    def _bounded_stream_text(response: object, max_bytes: int) -> str:
        chunks: list[bytes] = []
        total = 0
        iterator = getattr(response, "iter_content", None)
        if not callable(iterator):
            return ResearchManager._bounded_response_text(response, max_bytes)
        try:
            for chunk in iterator(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if not isinstance(chunk, bytes):
                    chunk = bytes(chunk)
                total += len(chunk)
                if total > max_bytes:
                    return ""
                chunks.append(chunk)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        encoding = getattr(response, "encoding", None) or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")

    @staticmethod
    def _canonical_url(url: str) -> str:
        try:
            parsed = urlparse(url)
            if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
                return ""
            port_value = parsed.port
            port = f":{port_value}" if port_value else ""
            path = parsed.path.rstrip("/") or "/"
            return f"{parsed.scheme.casefold()}://{parsed.hostname.casefold()}{port}{path}?{parsed.query}".rstrip("?")
        except (ValueError, OverflowError):
            return ""

    @staticmethod
    def _is_public_http_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme.casefold() not in {"http", "https"}:
                return False
            if parsed.username or parsed.password:
                return False
            host = (parsed.hostname or "").rstrip(".").casefold()
            if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
                return False
            try:
                addresses = {ipaddress.ip_address(host)}
            except ValueError:
                try:
                    records = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
                    addresses = {
                        ipaddress.ip_address(record[4][0].split("%", 1)[0])
                        for record in records
                    }
                except (OSError, ValueError):
                    return False
            return bool(addresses) and all(address.is_global for address in addresses)
        except (ValueError, OverflowError):
            return False

    @staticmethod
    def _clean_url(url: str) -> str:
        if url.startswith("//"):
            url = "https:" + url
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc:
            redirected = parse_qs(parsed.query).get("uddg")
            if redirected:
                return unquote(redirected[0])
        return url
