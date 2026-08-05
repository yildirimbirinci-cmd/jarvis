from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse


HttpGet = Callable[..., object]


@dataclass(frozen=True, slots=True)
class ProviderSearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider:
    name = "provider"

    def __init__(
        self,
        *,
        http_get: HttpGet,
        user_agent: str,
        public_url_validator: Callable[[str], bool],
        clean_url: Callable[[str], str],
        canonical_url: Callable[[str], str],
        bounded_response_text: Callable[[object, int], str],
        max_html_bytes: int,
    ) -> None:
        self.http_get = http_get
        self.user_agent = user_agent
        self.public_url_validator = public_url_validator
        self.clean_url = clean_url
        self.canonical_url = canonical_url
        self.bounded_response_text = bounded_response_text
        self.max_html_bytes = max_html_bytes

    def search(
        self,
        query: str,
        max_results: int,
    ) -> list[ProviderSearchResult]:
        raise NotImplementedError
