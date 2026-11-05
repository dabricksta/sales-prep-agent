"""Web access for the agent.

FixtureWeb serves synthetic pages from data/fixtures so the project runs offline and
deterministically. LiveWeb fetches pages from the lead's own domain only, with SSRF
guards. Page text is ALWAYS treated as untrusted data by the agent.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from .config import Config
from .validation import normalize_url


class WebError(Exception):
    """Expected, instructive failure (shown to the model as a tool error)."""


def wrap_untrusted(text: str) -> str:
    """Fence off external content so the model can tell data from instructions."""
    safe = text.replace("</untrusted_content", "<\\/untrusted_content")
    return f"<untrusted_content>\n{safe}\n</untrusted_content>"


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


class FixtureWeb:
    def __init__(self, fixtures_dir: Path, page_char_limit: int = 6000) -> None:
        self.page_char_limit = page_char_limit
        self._by_domain: dict[str, list[dict[str, str]]] = {}
        self._by_url: dict[str, dict[str, str]] = {}
        for path in sorted(fixtures_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            pages = data["pages"]
            self._by_domain[data["domain"].lower()] = pages
            for page in pages:
                self._by_url[normalize_url(page["url"])] = page

    def search(self, query: str, domain: str, limit: int = 5) -> list[dict[str, str]]:
        """Rank this company's pages (plus third-party coverage of it) by keyword overlap."""
        q = _tokens(query)
        if not q:
            raise WebError("query is empty")
        scored = []
        for page in self._by_domain.get(domain.lower(), []):
            overlap = len(q & _tokens(page["title"] + " " + page["text"]))
            if overlap:
                scored.append((overlap, page))
        scored.sort(key=lambda pair: -pair[0])
        return [
            {"url": p["url"], "title": p["title"], "snippet": p["text"][:160]}
            for _, p in scored[:limit]
        ]

    def fetch(self, url: str, domain: str) -> dict[str, str]:
        page = self._by_url.get(normalize_url(url))
        allowed = {normalize_url(p["url"]) for p in self._by_domain.get(domain.lower(), [])}
        if page is None or normalize_url(url) not in allowed:
            raise WebError(f"404: no page found at {url}. Use search_web to discover valid URLs.")
        return {"url": page["url"], "title": page["title"], "text": page["text"][: self.page_char_limit]}


# ---------------------------------------------------------------- live mode


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.title = ""
        self._in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def extract_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.title.strip(), "\n".join(parser.parts)


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        raise WebError(f"could not resolve host {host}") from exc


class LiveWeb:
    """Fetch-only access to the lead's own domain. No general search.

    Known limitation: DNS is resolved and checked before connecting, which does not
    fully close a DNS-rebinding window. For production, fetch through an egress proxy
    that enforces the same policy at connect time.
    """

    MAX_BYTES = 1_000_000
    MAX_REDIRECTS = 3

    def __init__(
        self,
        allowed_domain: str,
        page_char_limit: int = 6000,
        timeout: float = 10.0,
        resolver=_resolve,
        transport: Any = None,
    ) -> None:
        self.allowed_domain = allowed_domain.lower()
        self.page_char_limit = page_char_limit
        self.timeout = timeout
        self._resolver = resolver
        self._transport = transport  # injectable httpx transport (used by tests)

    def search(self, query: str, domain: str, limit: int = 5) -> list[dict[str, str]]:
        raise WebError("search_web is unavailable in live mode. Use fetch_page on pages of the lead's own site (try /, /about, /careers).")

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise WebError("only http and https URLs are allowed")
        host = (parts.hostname or "").lower()
        if not host:
            raise WebError("URL has no host")
        if host != self.allowed_domain and not host.endswith("." + self.allowed_domain):
            raise WebError(f"only pages on {self.allowed_domain} can be fetched in live mode")
        ips = self._resolver(host)
        if not ips or not all(is_public_ip(ip) for ip in ips):
            raise WebError("host resolves to a non-public address; blocked")

    def fetch(self, url: str, domain: str) -> dict[str, str]:
        import httpx

        current = url
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=False,
            headers={"User-Agent": "salesprep-agent/0.1"},
            transport=self._transport,
        ) as client:
            for _ in range(self.MAX_REDIRECTS + 1):
                self._check_url(current)
                try:
                    with client.stream("GET", current) as resp:
                        if resp.is_redirect:
                            location = resp.headers.get("location")
                            if not location:
                                raise WebError("redirect without Location header")
                            current = urljoin(current, location)
                            continue
                        if resp.status_code >= 400:
                            raise WebError(f"HTTP {resp.status_code} fetching {current}")
                        ctype = resp.headers.get("content-type", "")
                        if "html" not in ctype and "text" not in ctype:
                            raise WebError(f"unsupported content type: {ctype or 'unknown'}")
                        body = b""
                        for chunk in resp.iter_bytes():
                            body += chunk
                            if len(body) > self.MAX_BYTES:
                                break
                except httpx.HTTPError as exc:
                    raise WebError(f"network error: {type(exc).__name__}") from exc
                title, text = extract_text(body.decode("utf-8", errors="replace"))
                return {"url": current, "title": title or current, "text": text[: self.page_char_limit]}
        raise WebError("too many redirects")


def make_web(config: Config, domain: str) -> Any:
    if config.web_mode == "live":
        return LiveWeb(domain, page_char_limit=config.page_char_limit)
    return FixtureWeb(config.fixtures_dir, page_char_limit=config.page_char_limit)
